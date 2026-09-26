"""Turns a baseline and a stream of events into a judgement, stated in words.

Every assessment carries the reasoning that produced it. That is not a
courtesy. A caregiver who cannot tell why their phone went off learns to
ignore it, and an alert that gets ignored is worse than no alert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from .clock import clock, local_hour, midnight_before
from datetime import datetime, timedelta

from .model import (
    ONLINE,
    DeviceRoster,
    interior_events,
    is_down,
    outage_spans,
    transit_events,
)
from .rhythm import (
    ANY_INTERIOR,
    DEPARTURE_LOOKAHEAD,
    DEPARTURE_LOOKBACK,
    WINDOWS,
    Baseline,
    daytype_of,
    human_duration,
)

NORMAL = "NORMAL"
QUIET = "QUIET"
CONCERN = "CONCERN"
ALERT = "ALERT"
AWAY = "AWAY"
UNKNOWN = "UNKNOWN"

BLIND = "blind"
NO_HISTORY = "no_history"
NO_BASELINE = "no_baseline"
NO_CONTACT = "no_contact"

LADDER = (NORMAL, QUIET, CONCERN, ALERT)
STATES = LADDER + (AWAY, UNKNOWN)

QUIET_AT = 1.0
CONCERN_AT = 1.5
ALERT_AT = 2.5

MAX_ANCHOR_REASONS = 2

# How busy a camera normally is at this hour before its silence counts as
# explained by the camera being dead rather than by the person being still.
BLIND_SPOT_RATE = 0.5

# A camera that sees nothing sends nothing, so silence and a broken feed look
# identical from motion alone. Where the cameras report they are alive, that
# heartbeat is the difference: no heartbeat means we are not being told
# anything, and a system that cannot hear must not claim somebody has stopped
# moving. Where no heartbeat has ever arrived, nothing is assumed.
CONTACT_GAP_SECONDS = 45 * 60

# Being out much longer than usual is worth showing, not worth shouting about.
# Somebody visiting their sister for the day must not set off a phone. Past a
# full day with no return, that reasoning stops holding.
ABSENCE_ALARM_SECONDS = 24 * 3600


@dataclass
class Assessment:
    at: datetime
    state: str
    headline: str
    reasons: list = field(default_factory=list)
    silence_seconds: float = None
    silence_began: datetime = None
    threshold_seconds: float = None
    ratio: float = None
    last_device: str = None
    last_device_name: str = None
    departure_at: datetime = None
    departure_device: str = None
    missed_anchors: list = field(default_factory=list)
    devices_down: list = field(default_factory=list)
    absence_unusual: bool = False
    capped_by_outage: bool = False
    unknown_reason: str = None

    @property
    def rung(self):
        """Position on the ladder, or None for the states beside it."""
        return LADDER.index(self.state) if self.state in LADDER else None

    @property
    def needs_attention(self):
        return self.state in (CONCERN, ALERT)

    def to_dict(self):
        return {
            "at": self.at.isoformat(),
            "state": self.state,
            "headline": self.headline,
            "reasons": list(self.reasons),
            "silence_seconds": self.silence_seconds,
            "silence_began": self.silence_began.isoformat() if self.silence_began else None,
            "threshold_seconds": self.threshold_seconds,
            "ratio": round(self.ratio, 3) if self.ratio is not None else None,
            "last_device": self.last_device,
            "last_device_name": self.last_device_name,
            "departure_at": self.departure_at.isoformat() if self.departure_at else None,
            "departure_device": self.departure_device,
            "missed_anchors": [
                {"device_id": a.device_id, "window": a.window, "usual": a.clock()}
                for a in self.missed_anchors
            ],
            "devices_down": list(self.devices_down),
            "absence_unusual": self.absence_unusual,
            "capped_by_outage": self.capped_by_outage,
            "unknown_reason": self.unknown_reason,
        }


def _clock(moment):
    return clock(moment)


def _down_now(spans, device_ids, now):
    return [
        device_id
        for device_id in device_ids
        if is_down(spans, device_id, now, now + timedelta(seconds=1))
    ]


def _window_bounds(name):
    for window, low, high in WINDOWS:
        if window == name:
            return low, high
    return 0, 1440


def _missed_anchors(baseline, roster, inside, spans, now, began):
    """Anchors that fell due during the current silence and did not happen.

    A habit missed before the silence began says nothing about it. Someone who
    slept through their usual morning and has been busy all afternoon is not
    made more worrying by that morning when they sit still after lunch.
    """
    midnight = midnight_before(now)
    minute_now = (now - midnight).total_seconds() / 60.0
    today = [event for event in inside if event.at >= midnight]

    missed = []
    for anchor in baseline.anchors_for(daytype_of(now)):
        if anchor.late_by(minute_now) <= 0:
            continue
        due = midnight + timedelta(minutes=minute_now - anchor.late_by(minute_now))
        if due < began:
            continue
        low, high = _window_bounds(anchor.window)

        # A camera that was down cannot have seen the habit it usually sees,
        # so its silence says nothing about the person.
        if anchor.device_id != ANY_INTERIOR and is_down(
            spans,
            anchor.device_id,
            midnight + timedelta(minutes=low),
            min(now, midnight + timedelta(minutes=high)),
        ):
            continue

        seen = any(
            low <= event.minute_of_day < high
            and (anchor.device_id == ANY_INTERIOR or event.device_id == anchor.device_id)
            for event in today
        )
        if not seen:
            missed.append(anchor)
    return sorted(missed, key=lambda anchor: anchor.median_minute)


def _unknown(now, reason, headline, reasons, down=()):
    return Assessment(at=now, state=UNKNOWN, headline=headline,
                      reasons=list(reasons), devices_down=list(down),
                      unknown_reason=reason)


def _away_assessment(now, roster, baseline, last, departure, silence, threshold, ratio, down):
    began = last.at
    limit = baseline.tolerated_absence(daytype_of(began), local_hour(began))
    door = roster.name(departure.device_id)

    reasons = [
        "The %s reported activity at %s, right as the house went quiet."
        % (door, _clock(departure.at)),
        "Nothing inside has moved since %s, which is what an empty house looks like."
        % _clock(began),
    ]

    unusual = limit is not None and silence > limit
    if limit is not None:
        reasons.append("Trips out that begin around this time normally run under %s."
                       % human_duration(limit))

    if silence > ABSENCE_ALARM_SECONDS:
        state = CONCERN
        headline = "Out since %s, now %s, with no sign of a return." % (
            _clock(began), human_duration(silence))
        reasons.append("More than a day out with nobody coming back is worth a call.")
    elif unusual:
        state = AWAY
        headline = "Out since %s, now %s, which is longer than she is usually out." % (
            _clock(began), human_duration(silence))
    else:
        state = AWAY
        headline = "Out since %s. The %s opened, so the quiet is expected." % (
            _clock(began), door)

    return Assessment(
        at=now, state=state, headline=headline, reasons=reasons,
        silence_seconds=silence, silence_began=began,
        threshold_seconds=threshold, ratio=ratio,
        last_device=last.device_id, last_device_name=roster.name(last.device_id),
        departure_at=departure.at, departure_device=departure.device_id,
        absence_unusual=unusual,
        devices_down=list(down),
    )


def _ladder_state(ratio, missed):
    if ratio < QUIET_AT:
        state = NORMAL
    elif ratio < CONCERN_AT:
        state = QUIET
    elif ratio < ALERT_AT:
        state = CONCERN
    else:
        state = ALERT

    # A habit kept almost every day, missed, is worth more than general quiet,
    # so it carries concern up to alert. It does not lift merely quiet, because
    # someone sleeping in is late for their habits and perfectly fine.
    if missed and state == CONCERN:
        state = ALERT
    return state


def assess(events, baseline, roster, now):
    """Judge the household as at a single moment."""
    events = [event for event in events if event.at <= now]
    spans = outage_spans(events)
    interior_ids = roster.interior_ids()
    down = _down_now(spans, interior_ids, now)

    heartbeats = [event.at for event in events if event.kind == ONLINE]
    if heartbeats and (now - max(heartbeats)).total_seconds() > CONTACT_GAP_SECONDS:
        quiet_for = human_duration((now - max(heartbeats)).total_seconds())
        return _unknown(
            now,
            NO_CONTACT,
            "Cannot tell. Stillwatch has not heard from the cameras for %s." % quiet_for,
            ["The cameras report in regularly, and none has reported for %s." % quiet_for,
             "Nothing can be judged about the household until they are reachable again.",
             "This is a problem with the connection, not necessarily with anyone at home."],
            down,
        )

    if interior_ids and len(down) == len(interior_ids):
        return _unknown(
            now,
            BLIND,
            "Cannot tell. Every camera inside the house is offline.",
            ["Nothing can be judged while there is nothing reporting.",
             "Offline: %s." % ", ".join(roster.name(d) for d in down)],
            down,
        )

    inside = interior_events(events, roster)
    if not inside:
        return _unknown(now, NO_HISTORY, "Cannot tell. No movement has ever been recorded inside.",
                        ["There is no interior history to compare against."], down)

    last = inside[-1]
    began = last.at
    silence = (now - began).total_seconds()
    began_daytype = daytype_of(began)
    threshold = baseline.tolerated_quiet(began_daytype, local_hour(began))

    if threshold is None or not threshold:
        return _unknown(
            now,
            NO_BASELINE,
            "Cannot tell yet. There is not enough history for this time of day.",
            ["Last movement was in the %s at %s." % (roster.name(last.device_id), _clock(began)),
             "A baseline for a %s at %02d:00 has not been learned."
             % (began_daytype, local_hour(began))],
            down,
        )

    ratio = silence / threshold
    missed = _missed_anchors(baseline, roster, inside, spans, now, began)

    lookback = began - timedelta(seconds=DEPARTURE_LOOKBACK)
    lookahead = min(now, began + timedelta(seconds=DEPARTURE_LOOKAHEAD))
    # Only a door event at the start of the silence explains it. A caller
    # ringing the bell hours later is not someone coming home.
    departures = [event for event in transit_events(events, roster)
                  if lookback <= event.at <= lookahead]

    if departures:
        return _away_assessment(now, roster, baseline, last, departures[-1],
                                silence, threshold, ratio, down)

    state = _ladder_state(ratio, missed)

    # A dead camera in a room she would normally be using is a likelier
    # explanation for the silence than a collapse, so the alarm is held back
    # and the camera is named instead. A dead camera in a room she rarely uses
    # at this hour explains nothing, and the alert stands. Otherwise one flaky
    # camera anywhere in the house would switch off alerting altogether.
    blind_spots = [
        device_id for device_id in down
        if baseline.activity_rate(device_id, daytype_of(now), local_hour(now))
        >= BLIND_SPOT_RATE
    ]
    capped = bool(blind_spots) and state == ALERT
    if capped:
        state = CONCERN

    room = roster.name(last.device_id)

    reasons = [
        "Last movement was in the %s at %s, %s ago."
        % (room, _clock(began), human_duration(silence)),
        baseline.describe_quiet(began_daytype, local_hour(began)).capitalize() + ".",
        "No door has been used since the house went quiet, so she is at home.",
    ]
    for anchor in missed[:MAX_ANCHOR_REASONS]:
        share = round(anchor.hit_rate * 100)
        if anchor.device_id == ANY_INTERIOR:
            reasons.append(
                "She is normally moving about by around %s, on %d%% of days, and has not been."
                % (anchor.clock(), share))
        else:
            # Camera names are not all rooms. "the Bedroom Door camera" reads
            # correctly whatever the household called it.
            reasons.append(
                "The %s camera normally sees her by around %s, on %d%% of days, and has not."
                % (roster.name(anchor.device_id), anchor.clock(), share))
    spare = len(missed) - MAX_ANCHOR_REASONS
    if spare == 1:
        reasons.append("One other thing she usually does by now has not happened either.")
    elif spare > 1:
        reasons.append("%d other things she usually does by now have not happened either." % spare)
    if down:
        if capped:
            reasons.append(
                "Held back from an alert because the %s is offline and she is "
                "normally active there at this hour, so the camera could be the "
                "whole reason for the quiet. Worth checking it."
                % ", ".join(roster.name(d) for d in blind_spots))
        else:
            reasons.append("Offline and not counted: %s."
                           % ", ".join(roster.name(d) for d in down))

    if state == NORMAL:
        headline = "All normal. Last movement in the %s %s ago." % (room, human_duration(silence))
    elif state == QUIET:
        headline = "Quieter than usual. No movement for %s, against a usual %s at this hour." % (
            human_duration(silence), human_duration(threshold))
    else:
        headline = "No movement since %s, now %s, where %s is usual for a %s at that hour." % (
            _clock(began), human_duration(silence), human_duration(threshold), began_daytype)
        if missed:
            first = missed[0]
            label = "up and about" if first.device_id == ANY_INTERIOR else "in the %s" % roster.name(first.device_id)
            headline += " She is normally %s by %s." % (label, first.clock())

    return Assessment(
        at=now, state=state, headline=headline, reasons=reasons,
        silence_seconds=silence, silence_began=began,
        threshold_seconds=threshold, ratio=ratio,
        last_device=last.device_id, last_device_name=room,
        missed_anchors=missed, devices_down=list(down),
        capped_by_outage=capped,
    )


def walk(events, baseline, roster, start, end, step_minutes=15):
    """Assess repeatedly across a span, which is what a running service does."""
    readings = []
    moment = start
    step = timedelta(minutes=step_minutes)
    while moment <= end:
        readings.append(assess(events, baseline, roster, moment))
        moment += step
    return readings


def peak(readings):
    """The highest rung reached, and the latest reading that reached it."""
    climbed = [reading for reading in readings if reading.rung is not None]
    if not climbed:
        return None
    return max(climbed, key=lambda reading: (reading.rung, reading.at))


def changes(readings):
    """Only the readings where the state moved, which is what a log should hold."""
    moved = []
    previous = None
    for reading in readings:
        if reading.state != previous:
            moved.append(reading)
            previous = reading.state
    return moved
