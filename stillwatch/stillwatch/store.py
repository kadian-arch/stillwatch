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
import sqlite3
import threading
from datetime import datetime, timezone

from .model import DING, Device, DeviceRoster, Event, INTERIOR, TRANSIT, parse_timestamp

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
    """CREATE TABLE IF NOT EXISTS tokens (
        account    TEXT PRIMARY KEY,
        access     TEXT,
        refresh    TEXT,
        expires_at TEXT,
        linked_at  TEXT
    )""",
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

    @property
    def kind(self):
        return self.dialect.name

    def close(self):
        self._db.close()

    def _sql(self, statement):
        return statement.replace("?", self.dialect.placeholder)

    def _execute(self, statement, values=()):
        cursor = self._db.cursor()
        cursor.execute(self._sql(statement), values)
        return cursor

    def _fetchall(self, statement, values=()):
        cursor = self._execute(statement, values)
        rows = cursor.fetchall()
        cursor.close()
        return [dict(row) for row in rows]

    def add(self, event, raw=None):
        """True if this event was new, False if it was a redelivery."""
        return self.add_many([event], {event.event_id: raw} if raw else None) == 1

    def add_many(self, events, raws=None):
        if not events:
            return 0
        statement = (
            "INSERT INTO events (event_id, device_id, kind, at, raw)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (event_id) DO NOTHING"
        )
        stored = 0
        with self._lock:
            for event in events:
                cursor = self._execute(statement, (
                    event.event_id,
                    event.device_id,
                    event.kind,
                    event.at.isoformat(),
                    json.dumps((raws or {}).get(event.event_id)) if raws else None,
                ))
                stored += max(0, cursor.rowcount)
                cursor.close()
            self._db.commit()
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
            values.append(since.isoformat())
        if until is not None:
            clauses.append("at < ?")
            values.append(until.isoformat())
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

    def last_event_at(self):
        with self._lock:
            rows = self._fetchall("SELECT MAX(at) AS latest FROM events")
        latest = rows[0]["latest"] if rows else None
        return parse_timestamp(latest) if latest else None

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
