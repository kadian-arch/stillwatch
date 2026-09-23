"""Event and device types, and the adapter between a source and the engine.

Everything downstream works on these types. Swapping the simulator for the
Ring API means changing normalise and load_roster, and nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

TRANSIT = "transit"
INTERIOR = "interior"

MOTION = "motion"
DING = "ding"
OFFLINE = "device_offline"
ONLINE = "device_online"

PRESENCE_KINDS = (MOTION, DING)


@dataclass(frozen=True)
class Event:
    event_id: str
    device_id: str
    kind: str
    at: datetime

    @property
    def minute_of_day(self) -> float:
        return self.at.hour * 60 + self.at.minute + self.at.second / 60.0


@dataclass(frozen=True)
class Device:
    device_id: str
    name: str
    zone_class: str


class DeviceRoster:
    def __init__(self, devices):
        self._devices = {device.device_id: device for device in devices}

    def __contains__(self, device_id):
        return device_id in self._devices

    def __iter__(self):
        return iter(self._devices.values())

    def __len__(self):
        return len(self._devices)

    def get(self, device_id):
        return self._devices.get(device_id)

    def name(self, device_id):
        device = self._devices.get(device_id)
        return device.name if device else device_id

    def zone_class(self, device_id):
        device = self._devices.get(device_id)
        return device.zone_class if device else INTERIOR

    def is_interior(self, device_id):
        return self.zone_class(device_id) == INTERIOR

    def is_transit(self, device_id):
        return self.zone_class(device_id) == TRANSIT

    def interior_ids(self):
        return [d.device_id for d in self._devices.values() if d.zone_class == INTERIOR]

    def transit_ids(self):
        return [d.device_id for d in self._devices.values() if d.zone_class == TRANSIT]


def parse_timestamp(text):
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def normalise(record):
    """Map one source record onto an Event.

    The field names below are the simulator and Ring convention. A different
    source is handled here rather than anywhere downstream.
    """
    return Event(
        event_id=str(record["event_id"]),
        device_id=str(record["device_id"]),
        kind=str(record["kind"]),
        at=parse_timestamp(record["created_at"]),
    )


def load_events(path):
    events = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                events.append(normalise(json.loads(line)))
    events.sort(key=lambda event: event.at)
    return events


def load_roster(path):
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    entries = payload["devices"] if isinstance(payload, dict) else payload
    return DeviceRoster(
        Device(
            device_id=str(entry["device_id"]),
            name=str(entry.get("name", entry["device_id"])),
            zone_class=str(entry.get("zone_class", INTERIOR)),
        )
        for entry in entries
    )


def interior_events(events, roster):
    return [e for e in events if e.kind in PRESENCE_KINDS and roster.is_interior(e.device_id)]


def transit_events(events, roster):
    return [e for e in events if e.kind in PRESENCE_KINDS and roster.is_transit(e.device_id)]


def outage_spans(events):
    """Periods where a device reported itself down.

    A span left open at the end of the stream means the device is still down,
    and is returned with an end of None.
    """
    spans = []
    opened = {}
    for event in events:
        if event.kind == OFFLINE:
            opened.setdefault(event.device_id, event.at)
        elif event.kind == ONLINE and event.device_id in opened:
            spans.append((event.device_id, opened.pop(event.device_id), event.at))
    for device_id, start in opened.items():
        spans.append((device_id, start, None))
    return spans


def is_down(spans, device_id, start, end):
    """True if the device was down for any part of [start, end)."""
    for span_device, span_start, span_end in spans:
        if span_device != device_id:
            continue
        if span_end is not None and span_end <= start:
            continue
        if span_start >= end:
            continue
        return True
    return False


def last_covered_day(events, waking_hour=9):
    """The most recent day the stream actually reaches into.

    A stream almost always ends part way through a night, leaving a final date
    holding nothing but a bathroom trip. Judging that date would read the end
    of the file as silence, so a date counts only if it has activity after the
    hour anyone would be up.
    """
    latest = {}
    for event in events:
        day = event.at.date()
        latest[day] = max(latest.get(day, event.at), event.at)
    covered = [day for day, last in sorted(latest.items()) if last.hour >= waking_hour]
    return covered[-1] if covered else max(latest)
