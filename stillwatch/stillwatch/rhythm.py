"""Learns what normal looks like for one household.

Two things are learned. Rhythm is how often each device sees activity at each
hour of each kind of day. Tolerated quiet is how long this person is normally
silent at that hour, which is what turns a raw gap into a judgement.

Silence that follows someone walking out of the door is excluded from the
tolerated quiet statistics. An empty house is quiet for reasons that say
nothing about the person, and letting those stretches into the baseline would
raise the threshold until a real collapse fell underneath it.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from .clock import local, local_date, local_hour, midnight_on
from datetime import datetime, timedelta, timezone

from .model import (
    DeviceRoster,
    Event,
    PRESENCE_KINDS,
    interior_events,
    is_down,
    outage_spans,
    transit_events,
)

WEEKDAY = "weekday"
WEEKEND = "weekend"
DAYTYPES = (WEEKDAY, WEEKEND)

MIN_GAP_SAMPLES = 12
DEPARTURE_LOOKBACK = 300
DEPARTURE_LOOKAHEAD = 1800
ANCHOR_MIN_HIT_RATE = 0.7
ANCHOR_MIN_DAYS = 4
ANCHOR_MIN_SPREAD = 10.0
ANCHOR_MAX_SPREAD = 45.0
ONSET_QUIET = 30.0
ANY_INTERIOR = "any_interior"

WINDOWS = (
    ("morning", 300, 660),
    ("midday", 660, 900),
    ("evening", 900, 1320),
)

LEVELS = ("p50", "p90", "p95", "p99")


def daytype_of(moment):
    return WEEKEND if local(moment).weekday() >= 5 else WEEKDAY


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def median(values):
    return percentile(values, 0.5)


def median_absolute_deviation(values):
    centre = median(values)
    if centre is None:
        return None
    return median([abs(value - centre) for value in values])


@dataclass(frozen=True)
class Anchor:
    """An activity that happens on most days at roughly the same time."""

    device_id: str
    daytype: str
    window: str
    median_minute: float
    spread_minutes: float
    hit_rate: float
    observed_days: int

    def clock(self):
        return "%02d:%02d" % (int(self.median_minute) // 60, int(self.median_minute) % 60)

    def late_by(self, minute_of_day, tolerance=2.5):
        """Minutes past the point where this anchor should have happened."""
        due = self.median_minute + tolerance * max(self.spread_minutes, ANCHOR_MIN_SPREAD)
        return minute_of_day - due


@dataclass
class Baseline:
    days_observed: dict = field(default_factory=dict)
    excluded_absences: int = 0
    activity: dict = field(default_factory=dict)
    quiet: dict = field(default_factory=dict)
    absence: dict = field(default_factory=dict)
    anchors: list = field(default_factory=list)
    first_event: str = ""
    last_event: str = ""
    learned_at: str = ""

    @staticmethod
    def _activity_key(device_id, daytype, hour):
        return "%s|%s|%d" % (device_id, daytype, hour)

    @staticmethod
    def _quiet_key(daytype, hour):
        return "%s|%d" % (daytype, hour)

    def activity_rate(self, device_id, daytype, hour):
        cell = self.activity.get(self._activity_key(device_id, daytype, hour))
        return cell["rate"] if cell else 0.0

    def activity_days(self, device_id, daytype, hour):
        cell = self.activity.get(self._activity_key(device_id, daytype, hour))
        return cell["days"] if cell else 0

    def tolerated_quiet(self, daytype, hour, level="p95"):
        cell = self.quiet.get(self._quiet_key(daytype, hour))
        if not cell or cell.get(level) is None:
            return None
        return float(cell[level])

    def quiet_cell(self, daytype, hour):
        return self.quiet.get(self._quiet_key(daytype, hour))

    def tolerated_absence(self, daytype, hour, level="p99"):
        """How long this person is normally out, for trips beginning at this hour."""
        cell = self.absence.get(self._quiet_key(daytype, hour))
        if not cell or cell.get(level) is None:
            return None
        return float(cell[level])

    def anchors_for(self, daytype, window=None):
        return [
            anchor
            for anchor in self.anchors
            if anchor.daytype == daytype and (window is None or anchor.window == window)
        ]

    def describe_quiet(self, daytype, hour, level="p95"):
        seconds = self.tolerated_quiet(daytype, hour, level)
        if seconds is None:
            return "no baseline yet for %s at %02d:00" % (daytype, hour)
        cell = self.quiet_cell(daytype, hour)
        return "a silence beginning at %02d:00 on a %s normally runs no longer than %s (%d days, %s)" % (
            hour,
            daytype,
            human_duration(seconds),
            cell["n"],
            cell["basis"],
        )

    def to_dict(self):
        payload = asdict(self)
        payload["anchors"] = [asdict(anchor) for anchor in self.anchors]
        return payload

    @classmethod
    def from_dict(cls, payload):
        baseline = cls(
            days_observed=payload.get("days_observed", {}),
            excluded_absences=payload.get("excluded_absences", 0),
            activity=payload.get("activity", {}),
            quiet=payload.get("quiet", {}),
            absence=payload.get("absence", {}),
            anchors=[Anchor(**item) for item in payload.get("anchors", [])],
            first_event=payload.get("first_event", ""),
            last_event=payload.get("last_event", ""),
            learned_at=payload.get("learned_at", ""),
        )
        return baseline

    def save(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


def human_duration(seconds):
    if seconds is None:
        return "unknown"
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return "%d min" % minutes
    hours, rest = divmod(minutes, 60)
    if rest == 0:
        return "%dh" % hours
    return "%dh %02dm" % (hours, rest)


def _full_days(events, until):
    """Complete days only. The first day always starts mid morning, and the
    last is cut wherever the stream happens to stop."""
    dates = sorted({local_date(event.at) for event in events})
    if not dates:
        return []
    if until is not None:
        return [day for day in dates if day < local_date(until)][1:]
    return dates[1:-1] if len(dates) > 2 else dates


def _learn_activity(events, roster, days, spans):
    presence = [e for e in events if e.kind in PRESENCE_KINDS]
    seen = {}
    for event in presence:
        seen.setdefault((event.device_id, local_date(event.at)), set()).add(
            local_hour(event.at))

    tally = {}
    for device in roster:
        for day in days:
            label = daytype_of(midnight_on(day))
            hours = seen.get((device.device_id, day), set())
            for hour in range(24):
                start = midnight_on(day)
                start += timedelta(hours=hour)
                if is_down(spans, device.device_id, start, start + timedelta(hours=1)):
                    continue
                key = Baseline._activity_key(device.device_id, label, hour)
                bucket = tally.setdefault(key, [0, 0])
                bucket[1] += 1
                if hour in hours:
                    bucket[0] += 1

    return {
        key: {"rate": hits / total, "days": total}
        for key, (hits, total) in tally.items()
        if total
    }


def _collect_gaps(events, roster, spans, departure_window):
    """The longest interior silence beginning in each hour of each day.

    One sample per day per hour, not one per gap. The monitor asks how long
    the house has been silent, so the baseline has to describe the longest
    stretch this person normally produces, not the average distance between
    two events. Keyed by the hour the silence began, because a silence that
    starts at eleven at night is still a night silence at three in the
    morning.
    """
    inside = interior_events(events, roster)
    doors = [event.at for event in transit_events(events, roster)]
    interior_ids = roster.interior_ids()

    lookback, lookahead = departure_window
    longest = {}
    absences = {}
    excluded = 0
    for earlier, later in zip(inside, inside[1:]):
        start, end = earlier.at, later.at
        seconds = (end - start).total_seconds()
        if seconds <= 0:
            continue

        if all(is_down(spans, device_id, start, end) for device_id in interior_ids):
            continue

        key = (local_date(start), daytype_of(start), local_hour(start))

        if lookahead > 0:
            window_open = start - timedelta(seconds=lookback)
            window_shut = min(end, start + timedelta(seconds=lookahead))
            if any(window_open <= moment <= window_shut for moment in doors):
                excluded += 1
                absences[key] = max(absences.get(key, 0.0), seconds)
                continue

        longest[key] = max(longest.get(key, 0.0), seconds)

    def fold(cells):
        folded = {}
        for (_, daytype, hour), value in cells.items():
            folded.setdefault(Baseline._quiet_key(daytype, hour), []).append(value)
        return folded

    return fold(longest), fold(absences), excluded


def _gather(samples, daytypes, hour, reach):
    values = []
    for daytype in daytypes:
        for offset in range(-reach, reach + 1):
            values.extend(samples.get(Baseline._quiet_key(daytype, (hour + offset) % 24), []))
    return values


def _pool(samples, daytype, hour):
    """Widen the sample around an hour until there is enough to trust.

    The order matters. Neighbouring hours first, then the other kind of day,
    then a wider band of hours. Falling back to the whole history is last,
    because it mixes night into day and produces a threshold that belongs to
    neither.
    """
    others = [other for other in DAYTYPES if other != daytype]
    attempts = (
        (_gather(samples, [daytype], hour, 1), "this hour"),
        (_gather(samples, [daytype] + others, hour, 1), "this hour, either kind of day"),
        (_gather(samples, [daytype] + others, hour, 3), "a few hours either side"),
    )
    for values, basis in attempts:
        if len(values) >= MIN_GAP_SAMPLES:
            return values, basis

    everything = [value for bucket in samples.values() for value in bucket]
    return everything, "whole history"


def _learn_quiet(home_gaps):
    quiet = {}
    for daytype in DAYTYPES:
        for hour in range(24):
            values, basis = _pool(home_gaps, daytype, hour)
            if not values:
                continue
            cell = {"n": len(values), "basis": basis}
            for level in LEVELS:
                cell[level] = percentile(values, float(level[1:]) / 100.0)
            quiet[Baseline._quiet_key(daytype, hour)] = cell
    return quiet


def _learn_anchors(events, roster, days, spans):
    inside = interior_events(events, roster)
    by_day = {}
    for event in inside:
        by_day.setdefault(local_date(event.at), []).append(event)

    anchors = []
    candidates = roster.interior_ids() + [ANY_INTERIOR]
    for device_id in candidates:
        for daytype in DAYTYPES:
            matching = [
                day for day in days
                if daytype_of(midnight_on(day)) == daytype
            ]
            for window, low, high in WINDOWS:
                hits = []
                eligible = 0
                for day in matching:
                    start = midnight_on(day)
                    span_start = start + timedelta(minutes=low)
                    span_end = start + timedelta(minutes=high)
                    if device_id != ANY_INTERIOR and is_down(spans, device_id, span_start, span_end):
                        continue
                    eligible += 1
                    # A habit is when something starts, not the first reading
                    # after a window opens on something already under way.
                    # Otherwise sitting in the living room across eleven
                    # o'clock teaches it a habit of arriving at 11:04.
                    previous = None
                    for event in by_day.get(day, []):
                        if device_id not in (ANY_INTERIOR, event.device_id):
                            continue
                        minute = event.minute_of_day
                        if minute >= high:
                            break
                        if minute >= low and (previous is None or minute - previous >= ONSET_QUIET):
                            hits.append(minute)
                            break
                        previous = minute

                if eligible < ANCHOR_MIN_DAYS or len(hits) < ANCHOR_MIN_DAYS:
                    continue
                rate = len(hits) / eligible
                if rate < ANCHOR_MIN_HIT_RATE:
                    continue
                spread = max(median_absolute_deviation(hits), 1.0)
                # Something that wanders by most of an hour cannot tell us
                # anything is late, so it is not an anchor.
                if spread > ANCHOR_MAX_SPREAD:
                    continue
                anchors.append(
                    Anchor(
                        device_id=device_id,
                        daytype=daytype,
                        window=window,
                        median_minute=round(median(hits), 1),
                        spread_minutes=round(spread, 1),
                        hit_rate=round(rate, 3),
                        observed_days=eligible,
                    )
                )
    return anchors


def learn(events, roster, until=None, departure_window=None):
    """Build a baseline from history. Events at or after until are ignored."""
    if departure_window is None:
        departure_window = (DEPARTURE_LOOKBACK, DEPARTURE_LOOKAHEAD)
    if until is not None:
        events = [event for event in events if event.at < until]
    events = sorted(events, key=lambda event: event.at)
    if not events:
        return Baseline(learned_at=datetime.now(timezone.utc).isoformat())

    spans = outage_spans(events)
    days = _full_days(events, until)
    home_gaps, away_gaps, excluded = _collect_gaps(events, roster, spans, departure_window)

    counts = {daytype: 0 for daytype in DAYTYPES}
    for day in days:
        counts[daytype_of(midnight_on(day))] += 1

    return Baseline(
        days_observed=counts,
        excluded_absences=excluded,
        activity=_learn_activity(events, roster, days, spans),
        quiet=_learn_quiet(home_gaps),
        absence=_learn_quiet(away_gaps),
        anchors=_learn_anchors(events, roster, days, spans),
        first_event=events[0].at.isoformat(),
        last_event=events[-1].at.isoformat(),
        learned_at=datetime.now(timezone.utc).isoformat(),
    )
