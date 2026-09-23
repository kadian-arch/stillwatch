"""Turns a routine into a stream of device events."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from .devices import DEFAULT_DEVICES, Device, by_id
from .events import DING, MOTION, OFFLINE, ONLINE, Event
from .persona import ASLEEP, MARGARET, Persona

OUT = "out"
HALLWAY = "hallway"
ENTRY = "entry"
BACK_ENTRY = "back_entry"

DOOR_SECONDS = 120
WALK_SECONDS = 90
MIN_BLOCK_SECONDS = 60


@dataclass(frozen=True)
class Block:
    start: datetime
    end: datetime
    zone: str

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass(frozen=True)
class Outage:
    device_id: str
    start: datetime
    end: datetime


@dataclass
class DayPlan:
    day: date
    blocks: list = field(default_factory=list)
    dings: list = field(default_factory=list)


def _jitter(rng, mean, sd, lo=None, hi=None):
    value = rng.gauss(mean, sd) if sd > 0 else float(mean)
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _sample(rng, start, end, rate_per_hour):
    span = (end - start).total_seconds()
    if span <= 0 or rate_per_hour <= 0:
        return []
    mean_gap = 3600.0 / rate_per_hour
    stamps = []
    elapsed = rng.expovariate(1.0 / mean_gap)
    while elapsed < span:
        stamps.append(start + timedelta(seconds=elapsed))
        elapsed += rng.expovariate(1.0 / mean_gap)
    return stamps


def clip(blocks, start, end):
    """Remove the span [start, end) from a block list, splitting where needed."""
    kept = []
    for block in blocks:
        if block.end <= start or block.start >= end:
            kept.append(block)
            continue
        if block.start < start:
            kept.append(Block(block.start, start, block.zone))
        if block.end > end:
            kept.append(Block(end, block.end, block.zone))
    return [block for block in kept if block.seconds >= MIN_BLOCK_SECONDS]


class Simulator:
    def __init__(self, persona=MARGARET, devices=DEFAULT_DEVICES, seed=7, tz=timezone.utc):
        self.persona = persona
        self.devices = tuple(devices)
        self.seed = seed
        self.tz = tz
        self.rng = random.Random(seed)

    def midnight(self, day):
        return datetime.combine(day, time(0, 0), tzinfo=self.tz)

    def absence(self, blocks, depart, back):
        """Replace a span of the day with a trip out through one of the doors."""
        door = BACK_ENTRY if self.rng.random() < self.persona.back_door_share else ENTRY
        leave_end = depart + timedelta(seconds=DOOR_SECONDS)
        return_end = back + timedelta(seconds=DOOR_SECONDS)
        kept = clip(blocks, depart, return_end)
        kept.append(Block(depart, leave_end, door))
        kept.append(Block(leave_end, back, OUT))
        kept.append(Block(back, return_end, door))
        return sorted(kept, key=lambda block: block.start)

    def _lay_out(self, day, cursor):
        midnight = self.midnight(day)
        blocks = []
        for step in self.persona.routine_for(day):
            offset = _jitter(self.rng, step.start_min, step.start_jitter)
            duration = _jitter(self.rng, step.duration_min, step.duration_jitter, lo=5.0)
            start = midnight + timedelta(minutes=offset)
            if cursor is not None and start < cursor:
                start = cursor
            cursor = start + timedelta(minutes=duration)
            blocks.append(Block(start, cursor, step.zone))
        return blocks, cursor

    def _maybe_outing(self, day, blocks):
        chance = self.persona.outing_chance.get(self.persona.daytype(day), 0.0)
        if self.rng.random() >= chance:
            return blocks
        window_lo, window_hi = self.persona.outing_window
        depart = self.midnight(day) + timedelta(minutes=self.rng.uniform(window_lo, window_hi))
        back = depart + timedelta(minutes=self.rng.uniform(*self.persona.outing_minutes))
        return self.absence(blocks, depart, back)

    def _night_trips(self, blocks):
        extra = []
        for block in blocks:
            if block.zone != ASLEEP or block.seconds < 3 * 3600:
                continue
            trips = 0
            if self.rng.random() < self.persona.night_trip_chance:
                trips = 2 if self.rng.random() < 0.4 else 1
            for _ in range(trips):
                offset = self.rng.uniform(1800, block.seconds - 1800)
                start = block.start + timedelta(seconds=offset)
                length = timedelta(minutes=self.rng.uniform(4, 9))
                extra.append(Block(start, start + length, "bathroom"))
        return sorted(blocks + extra, key=lambda item: item.start)

    def _add_transitions(self, blocks):
        result = []
        ordered = sorted(blocks, key=lambda block: block.start)
        for index, block in enumerate(ordered):
            result.append(block)
            if index + 1 >= len(ordered):
                continue
            following = ordered[index + 1]
            if block.zone in (OUT, ASLEEP, HALLWAY) or following.zone in (OUT, ASLEEP):
                continue
            if block.zone == following.zone or block.seconds <= 4 * WALK_SECONDS:
                continue
            if (following.start - block.end).total_seconds() > 600:
                continue
            handover = block.end - timedelta(seconds=WALK_SECONDS)
            result[-1] = Block(block.start, handover, block.zone)
            result.append(Block(handover, block.end, HALLWAY))
        return result

    def _dings(self, day):
        if self.rng.random() >= self.persona.visitor_chance:
            return []
        return [self.midnight(day) + timedelta(minutes=self.rng.uniform(600, 1080))]

    def plan_day(self, day, cursor=None):
        blocks, cursor = self._lay_out(day, cursor)
        blocks = self._maybe_outing(day, blocks)
        blocks = self._night_trips(blocks)
        plan = DayPlan(day=day, blocks=[], dings=self._dings(day))
        for when in plan.dings:
            visit = Block(when, when + timedelta(seconds=DOOR_SECONDS), ENTRY)
            blocks = sorted(blocks + [visit], key=lambda block: block.start)
        plan.blocks = self._add_transitions(blocks)
        return plan, cursor

    def plan_days(self, start_day, days, day_hook=None):
        plans = []
        cursor = None
        for offset in range(days):
            plan, cursor = self.plan_day(start_day + timedelta(days=offset), cursor)
            if day_hook is not None:
                plan = day_hook(self, plan)
                if plan.blocks:
                    cursor = max(block.end for block in plan.blocks)
            plans.append(plan)
        return plans

    def _raw_from_blocks(self, blocks):
        raw = []
        for block in blocks:
            if block.zone in (OUT, ASLEEP):
                continue
            for device in self.devices:
                if block.zone not in device.covers:
                    continue
                for when in _sample(self.rng, block.start, block.end, device.motion_per_hour):
                    raw.append((when, device.device_id, MOTION))
        return raw

    def _apply_cooldown(self, raw):
        lookup = by_id(self.devices)
        last = {}
        kept = []
        for when, device_id, kind in sorted(raw, key=lambda item: item[0]):
            if kind == MOTION:
                previous = last.get(device_id)
                if previous is not None:
                    if (when - previous).total_seconds() < lookup[device_id].cooldown_s:
                        continue
                last[device_id] = when
            kept.append((when, device_id, kind))
        return kept

    def _apply_outages(self, raw, outages):
        if not outages:
            return raw
        kept = []
        for item in raw:
            hidden = False
            for outage in outages:
                if outage.device_id == item[1] and outage.start <= item[0] < outage.end:
                    hidden = True
                    break
            if not hidden:
                kept.append(item)
        for outage in outages:
            kept.append((outage.start, outage.device_id, OFFLINE))
            kept.append((outage.end, outage.device_id, ONLINE))
        return kept

    def _finalise(self, raw):
        lookup = by_id(self.devices)
        ordered = sorted(raw, key=lambda item: (item[0], item[1]))
        events = []
        for index, (when, device_id, kind) in enumerate(ordered, 1):
            events.append(
                Event(
                    event_id="evt_%06d" % index,
                    device_id=device_id,
                    device_name=lookup[device_id].name,
                    kind=kind,
                    created_at=when,
                )
            )
        return events

    def generate(self, start_day, days, day_hook=None, outages=()):
        raw = []
        for plan in self.plan_days(start_day, days, day_hook):
            raw.extend(self._raw_from_blocks(plan.blocks))
            for when in plan.dings:
                raw.append((when, "front_door", DING))
        raw = self._apply_cooldown(raw)
        raw = self._apply_outages(raw, outages)
        return self._finalise(raw)
