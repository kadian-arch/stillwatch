"""Where real events live once they arrive.

The webhook writes here and the engine reads from here, so an event survives a
restart and the two sides never have to be running at the same time. Writes are
idempotent on the event id, because Ring will redeliver a webhook it is not sure
we received, and a duplicated motion event would shorten a silence that never
actually broke.

Two databases, one class. SQLite for a laptop, Postgres in production, because
a hosted container's disk is wiped on every restart and a baseline needs weeks
of history to mean anything. The SQL is written once and the only differences
between the two, the placeholder character and how a connection is opened, live
in the small dialect classes below.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone

from .model import DING, Device, DeviceRoster, Event, INTERIOR, TRANSIT, parse_timestamp

log = logging.getLogger("stillwatch.store")

STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS events (
        event_id  TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        kind      TEXT NOT NULL,
        at        TEXT NOT NULL,
        raw       TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS events_at ON events (at)",
    """CREATE TABLE IF NOT EXISTS devices (
        device_id  TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        zone_class TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS answers (
        episode TEXT PRIMARY KEY,
        outcome TEXT NOT NULL,
        note TEXT,
        answered_by TEXT,
        daytype TEXT,
        hour INTEGER,
        seconds REAL,
        at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS meta (
        name TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tokens (
        account    TEXT PRIMARY KEY,
        access     TEXT,
        refresh    TEXT,
        expires_at TEXT,
        linked_at  TEXT
    )""",
)

# Run after the tables, each on its own, and each allowed to fail. A column
# that is already there is the ordinary case, not a problem, and there is no
# form of ADD COLUMN IF NOT EXISTS that both databases understand.
MIGRATIONS = (
    "ALTER TABLE answers ADD COLUMN answered_by TEXT",
)

POSTGRES_PREFIXES = ("postgres://", "postgresql://")


class SqliteDialect:
    name = "sqlite"
    placeholder = "?"

    def connect(self, target):
        connection = sqlite3.connect(target, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def row(self, cursor, values):
        return values


class PostgresDialect:
    name = "postgres"
    placeholder = "%s"

    def connect(self, target):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(target, row_factory=dict_row, autocommit=False)

    def row(self, cursor, values):
        return values


def dialect_for(target):
    return PostgresDialect() if str(target).startswith(POSTGRES_PREFIXES) else SqliteDialect()


class EventStore:
    def __init__(self, target=":memory:", dialect=None):
        self.target = str(target)
        self.dialect = dialect or dialect_for(self.target)
        self._lock = threading.Lock()
        # The webhook and the dashboard are different threads in one process.
        self._db = self.dialect.connect(self.target)
        with self._lock:
            for statement in STATEMENTS:
                self._execute(statement)
            self._db.commit()
            for statement in MIGRATIONS:
                try:
                    self._execute(statement).close()
                    self._db.commit()
                except Exception:
                    # Postgres abandons the whole transaction on a failed
                    # statement, so the rollback is what lets the next one run.
                    self._db.rollback()

    @property
    def kind(self):
        return self.dialect.name

    def close(self):
        self._db.close()

    def _sql(self, statement):
        return statement.replace("?", self.dialect.placeholder)

    def _recover(self):
        """Leave the connection usable again, reopening it if it has gone.

        True means the connection was replaced and the statement may be worth
        trying once more. False means the connection is alive and the
        statement itself was the problem, which repeating would not fix.
        """
        try:
            self._db.rollback()
            return False
        except Exception:
            pass
        try:
            self._db.close()
        except Exception:
            pass
        try:
            self._db = self.dialect.connect(self.target)
            for statement in STATEMENTS:
                cursor = self._db.cursor()
                cursor.execute(self._sql(statement), ())
                cursor.close()
            self._db.commit()
            return True
        except Exception:
            return False

    def _execute(self, statement, values=(), retry=True):
        """Run one statement, and do not let one failure end the process.

        Two things made this necessary, and neither needs anything unusual to
        happen. Postgres abandons the rest of a transaction after a failed
        statement, so without a rollback a single bad query turns into every
        later query failing, for as long as the process lives. And a managed
        database closes idle connections and moves them during maintenance, so
        the one connection opened at startup does not last forever.

        Either way the service went on running, answering, and judging
        nothing, until somebody restarted it. For a product whose whole job is
        to notice that nothing is happening, dying quietly is the one failure
        that must not be possible.
        """
        try:
            cursor = self._db.cursor()
            cursor.execute(self._sql(statement), values)
            return cursor
        except Exception:
            if self._recover() and retry:
                return self._execute(statement, values, retry=False)
            raise

    def _fetchall(self, statement, values=()):
        cursor = self._execute(statement, values)
        rows = cursor.fetchall()
        cursor.close()
        return [dict(row) for row in rows]

    def add(self, event, raw=None):
        """True if this event was new, False if it was a redelivery."""
        return self.add_many([event], {event.event_id: raw} if raw else None) == 1

    def _insert_event(self, statement, values):
        """One event, behind a savepoint, so its failure costs only itself.

        Postgres abandons the rest of a transaction after a failed statement.
        Without a savepoint around each row, one event the database will not
        store would throw away every good event delivered beside it, and a
        webhook carrying a day of movement would land nothing at all.

        Returns the number of rows written, or None if the database refused
        this one row and the batch should carry on without it.
        """
        try:
            cursor = self._db.cursor()
            cursor.execute("SAVEPOINT one_event")
            cursor.close()
        except Exception:
            # No savepoint to hide behind. Better to attempt the write and let
            # the caller's recovery deal with it than to store nothing.
            cursor = self._execute(statement, values)
            written = max(0, cursor.rowcount)
            cursor.close()
            return written

        try:
            cursor = self._db.cursor()
            cursor.execute(self._sql(statement), values)
            written = max(0, cursor.rowcount)
            cursor.close()
            cursor = self._db.cursor()
            cursor.execute("RELEASE SAVEPOINT one_event")
            cursor.close()
            return written
        except Exception as error:
            try:
                cursor = self._db.cursor()
                cursor.execute("ROLLBACK TO SAVEPOINT one_event")
                cursor.close()
            except Exception:
                # The transaction itself is gone, not just this row, so this
                # is the connection failing rather than bad data.
                raise
            log.warning("the database refused one event: %s", error)
            return None

    def add_many(self, events, raws=None):
        """Store what can be stored, and say how much that was.

        A batch is not all or nothing. Ring delivers several events at once
        and redelivers what it is unsure of, so one unstorable event must not
        be able to cost the rest, and must not make Ring retry a delivery
        that will fail again for the same reason.
        """
        if not events:
            return 0
        statement = (
            "INSERT INTO events (event_id, device_id, kind, at, raw)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (event_id) DO NOTHING"
        )
        stored = 0
        refused = []
        with self._lock:
            for event in events:
                written = self._insert_event(statement, (
                    event.event_id,
                    event.device_id,
                    event.kind,
                    event.at.astimezone(timezone.utc).isoformat(),
                    json.dumps((raws or {}).get(event.event_id)) if raws else None,
                ))
                if written is None:
                    refused.append(event.event_id)
                else:
                    stored += written
            self._db.commit()
        if refused:
            log.warning("stored %d of %d events; %d refused: %s",
                        stored, len(events), len(refused),
                        ", ".join(refused[:5]))
        # Ring's devices endpoint carries no device type, and its capabilities
        # endpoint does not mention doorbells either, so a camera's name is all
        # there is to classify on. Somebody ringing it is better evidence.
        for event in events:
            if event.kind == DING:
                self.note_doorbell(event.device_id)
        return stored

    def note_doorbell(self, device_id):
        """Mark a device as watching a way in or out. Never the other way."""
        with self._lock:
            cursor = self._execute(
                "UPDATE devices SET zone_class = ? WHERE device_id = ? AND zone_class <> ?",
                (TRANSIT, device_id, TRANSIT))
            changed = max(0, cursor.rowcount)
            cursor.close()
            self._db.commit()
        return changed > 0

    def events(self, since=None, until=None):
        clauses, values = [], []
        if since is not None:
            clauses.append("at >= ?")
            values.append(since.astimezone(timezone.utc).isoformat())
        if until is not None:
            clauses.append("at < ?")
            values.append(until.astimezone(timezone.utc).isoformat())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._fetchall(
                "SELECT event_id, device_id, kind, at FROM events%s ORDER BY at" % where,
                tuple(values),
            )
        return [
            Event(event_id=row["event_id"], device_id=row["device_id"],
                  kind=row["kind"], at=parse_timestamp(row["at"]))
            for row in rows
        ]

    def count(self):
        with self._lock:
            return self._fetchall("SELECT COUNT(*) AS total FROM events")[0]["total"]

    def first_event_at(self):
        """The oldest event, which is as far back as this house goes."""
        with self._lock:
            rows = self._fetchall("SELECT MIN(at) AS earliest FROM events")
        found = rows[0]["earliest"] if rows else None
        return parse_timestamp(found) if found else None

    def last_event_at(self, kinds=None):
        """The newest event, or the newest of certain kinds.

        A caller asking "when did anything last happen in this house" wants
        movement, not a device saying it is still switched on. Anything that
        tracks activity must say which kinds it means.
        """
        sql = "SELECT MAX(at) AS latest FROM events"
        values = ()
        if kinds:
            kinds = tuple(kinds)
            sql += " WHERE kind IN (%s)" % ", ".join("?" for _ in kinds)
            values = kinds
        with self._lock:
            rows = self._fetchall(sql, values)
        latest = rows[0]["latest"] if rows else None
        return parse_timestamp(latest) if latest else None

    def save_answer(self, episode, outcome, at, note=None, by=None,
                    daytype=None, hour=None, seconds=None):
        """What a caregiver said when they looked.

        An episode is a single stretch of worry, keyed by the moment the house
        went quiet. Answering one ends the chasing for it. Nothing is deleted:
        the answer stays so the same stretch is never raised twice, and so the
        household can see what was said and when.
        """
        with self._lock:
            self._execute(
                "INSERT INTO answers"
                " (episode, outcome, note, answered_by, daytype, hour, seconds, at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (episode) DO UPDATE SET outcome = excluded.outcome,"
                " note = excluded.note, answered_by = excluded.answered_by,"
                " at = excluded.at",
                (episode, outcome, note, by, daytype, hour, seconds, at.isoformat()),
            ).close()
            self._db.commit()

    def answer_for(self, episode):
        if not episode:
            return None
        with self._lock:
            rows = self._fetchall(
                "SELECT episode, outcome, note, answered_by AS by, at"
                " FROM answers WHERE episode = ?",
                (episode,))
        return rows[0] if rows else None

    def answers(self, limit=50):
        with self._lock:
            return self._fetchall(
                "SELECT episode, outcome, note, answered_by AS by, at"
                " FROM answers ORDER BY at DESC")[:limit]

    def confirmed_quiet(self):
        """Stretches the household has said are normal for them.

        These become samples in the baseline, so a person who has changed when
        they sleep stops being woken about by the second week rather than the
        tenth.
        """
        with self._lock:
            rows = self._fetchall(
                "SELECT daytype, hour, seconds FROM answers"
                " WHERE outcome = ? AND daytype IS NOT NULL AND seconds IS NOT NULL",
                ("expected",))
        return [(row["daytype"], int(row["hour"]), float(row["seconds"])) for row in rows]

    def save_state(self, name, payload):
        """Remember something across a restart, as JSON."""
        with self._lock:
            self._execute(
                "INSERT INTO meta (name, value) VALUES (?, ?)"
                " ON CONFLICT (name) DO UPDATE SET value = excluded.value",
                (name, json.dumps(payload)),
            ).close()
            self._db.commit()

    def load_state(self, name):
        with self._lock:
            rows = self._fetchall("SELECT value FROM meta WHERE name = ?", (name,))
        if not rows:
            return None
        try:
            return json.loads(rows[0]["value"])
        except ValueError:
            return None

    def note_delivery(self, at):
        """Remember that something was delivered to us, whatever it contained.

        Silence in the data and silence on the wire look the same from the
        events alone. This is the difference: it is written whenever a signed
        delivery is accepted, even an empty one, so the service can tell
        "nobody moved" from "nobody is telling us anything".
        """
        with self._lock:
            self._execute(
                "INSERT INTO meta (name, value) VALUES (?, ?)"
                " ON CONFLICT (name) DO UPDATE SET value = excluded.value",
                ("last_delivery_at", at.isoformat()),
            ).close()
            self._db.commit()

    def last_delivery_at(self):
        with self._lock:
            rows = self._fetchall("SELECT value FROM meta WHERE name = ?", ("last_delivery_at",))
        return parse_timestamp(rows[0]["value"]) if rows else None

    def remember_device(self, device):
        with self._lock:
            self._execute(
                "INSERT INTO devices (device_id, name, zone_class) VALUES (?, ?, ?)"
                " ON CONFLICT (device_id) DO UPDATE SET name = excluded.name,"
                " zone_class = CASE WHEN devices.zone_class = 'transit'"
                " THEN 'transit' ELSE excluded.zone_class END",
                (device.device_id, device.name, device.zone_class),
            ).close()
            self._db.commit()

    def remember_devices(self, devices):
        for device in devices:
            self.remember_device(device)

    def roster(self):
        with self._lock:
            rows = self._fetchall(
                "SELECT device_id, name, zone_class FROM devices ORDER BY zone_class, name"
            )
        return DeviceRoster(
            Device(row["device_id"], row["name"], row["zone_class"]) for row in rows
        )

    def save_tokens(self, account, access, refresh, expires_at):
        """Ring credentials live in the database, never in the repository or in
        a file that could be committed by accident."""
        with self._lock:
            self._execute(
                "INSERT INTO tokens (account, access, refresh, expires_at, linked_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (account) DO UPDATE SET access = excluded.access,"
                " refresh = excluded.refresh, expires_at = excluded.expires_at",
                (account, access, refresh,
                 expires_at.isoformat() if expires_at else None,
                 datetime.now(timezone.utc).isoformat()),
            ).close()
            self._db.commit()

    def tokens(self, account="default"):
        with self._lock:
            rows = self._fetchall(
                "SELECT access, refresh, expires_at, linked_at FROM tokens WHERE account = ?",
                (account,),
            )
        if not rows:
            return None
        row = rows[0]
        return {
            "access": row["access"],
            "refresh": row["refresh"],
            "expires_at": parse_timestamp(row["expires_at"]) if row["expires_at"] else None,
            "linked_at": parse_timestamp(row["linked_at"]) if row["linked_at"] else None,
        }

    def is_linked(self, account="default"):
        stored = self.tokens(account)
        return bool(stored and stored.get("refresh"))
