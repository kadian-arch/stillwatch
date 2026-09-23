"""Fetching the past.

A baseline needs weeks of history before it can say anything useful about a
person, and nobody is going to wait weeks after installing. The moment an
account is linked we ask Ring for everything it already holds, so the first
useful judgement comes on day one instead of in November.

One device failing must never cost us the others. A household where the porch
camera errors should still learn its kitchen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .ring import RingClient, RingError

DEFAULT_DAYS = 30


@dataclass
class BackfillReport:
    devices: int = 0
    fetched: int = 0
    stored: int = 0
    per_device: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)
    first_event: datetime = None
    last_event: datetime = None

    @property
    def ok(self):
        return not self.failures

    def span_days(self):
        if not self.first_event or not self.last_event:
            return 0.0
        return round((self.last_event - self.first_event).total_seconds() / 86400.0, 1)

    def to_dict(self):
        return {
            "devices": self.devices,
            "fetched": self.fetched,
            "stored": self.stored,
            "per_device": dict(self.per_device),
            "failures": list(self.failures),
            "first_event": self.first_event.isoformat() if self.first_event else None,
            "last_event": self.last_event.isoformat() if self.last_event else None,
            "span_days": self.span_days(),
        }

    def summary(self):
        lines = ["%d devices, %d events read, %d of them new"
                 % (self.devices, self.fetched, self.stored)]
        if self.first_event and self.last_event:
            lines.append("  history spans %.1f days, %s to %s"
                         % (self.span_days(),
                            self.first_event.strftime("%Y-%m-%d %H:%M"),
                            self.last_event.strftime("%Y-%m-%d %H:%M")))
        for device_id, count in sorted(self.per_device.items()):
            lines.append("  %-16s %d" % (device_id, count))
        for failure in self.failures:
            lines.append("  could not read %s: %s" % failure)
        return "\n".join(lines)


def client_from_store(store, environ=None, account="default"):
    """A Ring client carrying whatever credentials the linking step saved."""
    environ = os.environ if environ is None else environ
    client_id = environ.get("STILLWATCH_RING_CLIENT_ID", "").strip()
    client_secret = environ.get("STILLWATCH_RING_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RingError("no Ring client credentials configured")

    stored = store.tokens(account) or {}
    if not stored.get("refresh"):
        raise RingError("this home has not been linked to a Ring account yet")

    return RingClient(
        client_id,
        client_secret,
        refresh_token=stored.get("refresh"),
        access_token=stored.get("access"),
        expires_at=stored.get("expires_at"),
    )


def backfill(client, store, since=None, days=DEFAULT_DAYS, account="default", on_progress=None):
    """Read history for every device on the account into the store."""
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=days)

    report = BackfillReport()

    try:
        devices = client.devices()
    except RingError as error:
        report.failures.append(("the device list", str(error)))
        return report

    store.remember_devices(devices)
    report.devices = len(devices)

    for device in devices:
        try:
            events = client.history(device.device_id, since=since)
        except RingError as error:
            # One unreadable camera must not cost us the rest of the house.
            report.failures.append((device.device_id, str(error)))
            continue

        stored = store.add_many(events)
        report.fetched += len(events)
        report.stored += stored
        report.per_device[device.device_id] = len(events)

        for event in events:
            if report.first_event is None or event.at < report.first_event:
                report.first_event = event.at
            if report.last_event is None or event.at > report.last_event:
                report.last_event = event.at

        if on_progress is not None:
            on_progress(device, len(events), stored)

    # Keep the freshly saved token, so the next run starts already authorised.
    if client.refresh_token:
        store.save_tokens(account, client.access_token, client.refresh_token, client.expires_at)

    return report
