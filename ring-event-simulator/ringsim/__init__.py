"""Ring style event simulator for absence and inactivity detection."""

from .devices import DEFAULT_DEVICES, INTERIOR, TRANSIT, Device, covering, manifest
from .engine import Block, DayPlan, Outage, Simulator
from .events import DING, MOTION, OFFLINE, ONLINE, Event, read_jsonl, write_jsonl
from .persona import MARGARETTE, PERSONAS, Persona, RoutineStep
from .scenarios import ALERT, NO_ALERT, SCENARIOS, Scenario

__version__ = "0.1.0"

__all__ = [
    "ALERT",
    "DEFAULT_DEVICES",
    "DING",
    "INTERIOR",
    "MARGARETTE",
    "MOTION",
    "NO_ALERT",
    "OFFLINE",
    "ONLINE",
    "PERSONAS",
    "SCENARIOS",
    "TRANSIT",
    "Block",
    "DayPlan",
    "Device",
    "Event",
    "Outage",
    "Persona",
    "RoutineStep",
    "Scenario",
    "Simulator",
    "covering",
    "manifest",
    "read_jsonl",
    "write_jsonl",
]
