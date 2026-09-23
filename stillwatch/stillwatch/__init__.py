"""Stillwatch: inactivity detection for a person living alone."""

from .model import (
    INTERIOR,
    TRANSIT,
    Device,
    DeviceRoster,
    Event,
    load_events,
    load_roster,
    outage_spans,
)
from .rhythm import Anchor, Baseline, daytype_of, human_duration, learn

__version__ = "0.1.0"

__all__ = [
    "INTERIOR",
    "TRANSIT",
    "Anchor",
    "Baseline",
    "Device",
    "DeviceRoster",
    "Event",
    "daytype_of",
    "human_duration",
    "learn",
    "load_events",
    "load_roster",
    "outage_spans",
]
