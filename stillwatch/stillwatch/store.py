"""Where real events live once they arrive.

The webhook writes here and the engine reads from here, so an event survives a
restart and the two sides never have to be running at the same time. Writes are
idempotent on the event id, because Ring will redeliver a webhook it is not sure
we received, and a duplicated motion event would shorten a silence that never
actually broke.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .model import Device, DeviceRoster, Event, INTERIOR, parse_timestamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id  TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    kind      TEXT NOT NULL,
    at        TEXT NOT NULL,
    raw       TEXT
);
CREATE INDEX IF NOT EXISTS events_at ON events (at);

CREATE TABLE IF NOT EXISTS devices (
    device_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    zone_class TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    account    TEXT PRIMARY KEY,
    access     TEXT,
    refresh    TEXT,
    expires_at TEXT,
    linked_at  TEXT
);
"""


class EventStore:
    def __init__(self, path=":memory:"):
        self.path = str(path)
        self._lock = threading.Lock()
        # The webhook and the dashboard are different threads in one process.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self):
        self._db.close()

    def add(self, event, raw=None):
        """True if this event was new, False if it was a redelivery."""
        return self.add_many([event], {event.event_id: raw} if raw else None) == 1

    def add_many(self, events, raws=None):
        rows = [
            (
                event.event_id,
                event.device_id,
                event.kind,
                event.at.isoformat(),
                json.dumps((raws or {}).get(event.event_id)) if raws else None,
            )
            for event in events
        ]
        if not rows:
            return 0
        with self._lock:
            before = self._db.total_changes
            self._db.executemany(
                "INSERT OR IGNORE INTO events (event_id, device_id, kind, at, raw)"
                " VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._db.commit()
            return self._db.total_changes - before

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
            rows = self._db.execute(
                "SELECT event_id, device_id, kind, at FROM events%s ORDER BY at" % where,
                values,
            ).fetchall()
        return [
            Event(event_id=row["event_id"], device_id=row["device_id"],
                  kind=row["kind"], at=parse_timestamp(row["at"]))
            for row in rows
        ]

    def count(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def last_event_at(self):
        with self._lock:
            row = self._db.execute("SELECT MAX(at) FROM events").fetchone()
        return parse_timestamp(row[0]) if row and row[0] else None

    def remember_device(self, device):
        with self._lock:
            self._db.execute(
                "INSERT INTO devices (device_id, name, zone_class) VALUES (?, ?, ?)"
                " ON CONFLICT(device_id) DO UPDATE SET name = excluded.name,"
                " zone_class = excluded.zone_class",
                (device.device_id, device.name, device.zone_class),
            )
            self._db.commit()

    def remember_devices(self, devices):
        for device in devices:
            self.remember_device(device)

    def roster(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT device_id, name, zone_class FROM devices ORDER BY zone_class, name"
            ).fetchall()
        return DeviceRoster(
            Device(row["device_id"], row["name"], row["zone_class"]) for row in rows
        )

    def save_tokens(self, account, access, refresh, expires_at):
        """Ring credentials live in the database, never in the repo or a file
        that could be committed by accident."""
        with self._lock:
            self._db.execute(
                "INSERT INTO tokens (account, access, refresh, expires_at, linked_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(account) DO UPDATE SET access = excluded.access,"
                " refresh = excluded.refresh, expires_at = excluded.expires_at",
                (account, access, refresh,
                 expires_at.isoformat() if expires_at else None,
                 datetime.now(timezone.utc).isoformat()),
            )
            self._db.commit()

    def tokens(self, account="default"):
        with self._lock:
            row = self._db.execute(
                "SELECT access, refresh, expires_at, linked_at FROM tokens WHERE account = ?",
                (account,),
            ).fetchone()
        if not row:
            return None
        return {
            "access": row["access"],
            "refresh": row["refresh"],
            "expires_at": parse_timestamp(row["expires_at"]) if row["expires_at"] else None,
            "linked_at": parse_timestamp(row["linked_at"]) if row["linked_at"] else None,
        }

    def is_linked(self, account="default"):
        stored = self.tokens(account)
        return bool(stored and stored.get("refresh"))
