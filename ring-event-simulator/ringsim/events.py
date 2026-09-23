"""Event records and serialisation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Iterable

MOTION = "motion"
DING = "ding"
OFFLINE = "device_offline"
ONLINE = "device_online"

KINDS = (MOTION, DING, OFFLINE, ONLINE)


@dataclass(frozen=True)
class Event:
    event_id: str
    device_id: str
    device_name: str
    kind: str
    created_at: datetime

    def to_record(self) -> dict:
        return {
            "event_id": self.event_id,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "kind": self.kind,
            "created_at": self.created_at.isoformat(),
            "source": "simulator",
        }


def write_jsonl(events: Iterable[Event], stream: IO[str]) -> int:
    count = 0
    for event in events:
        stream.write(json.dumps(event.to_record()) + "\n")
        count += 1
    return count


def read_jsonl(stream: IO[str]) -> list[dict]:
    return [json.loads(line) for line in stream if line.strip()]
