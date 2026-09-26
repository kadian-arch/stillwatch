"""Checks on the silence monitor. Run directly, exits non zero on failure."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Clocks are pinned, so a suite cannot pass or fail depending on where the
# machine running it happens to be.
os.environ["STILLWATCH_TZ"] = "UTC"
sys.path.insert(0, str(ROOT.parent / "ring-event-simulator"))

from ringsim import Simulator
from ringsim.devices import DEFAULT_DEVICES, manifest
from ringsim.scenarios import ALERT as EXPECT_ALERT
from ringsim.scenarios import NO_ALERT as EXPECT_NO_ALERT
from ringsim.scenarios import SCENARIOS

from stillwatch.model import Device, DeviceRoster, Event, normalise
from stillwatch.monitor import (
    ALERT,
    AWAY,
    CONCERN,
    NORMAL,
    QUIET,
    UNKNOWN,
    assess,
    changes,
    peak,
    walk,
)
from stillwatch.rhythm import Baseline, learn

START = date(2026, 8, 24)
DAYS = 28
SEED = 7

PASSED = 0
FAILED = []


def check(label, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print("  pass  %s" % label)
    else:
        FAILED.append(label)
        print("  FAIL  %s  %s" % (label, detail))


def section(title):
    print("\n%s" % title)


def roster():
    return DeviceRoster(
        Device(entry["device_id"], entry["name"], entry["zone_class"])
        for entry in manifest(DEFAULT_DEVICES)
    )


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=timezone.utc)


def prepared(key):
    """Events, baseline learned from history before the target day, and that day."""
    events, meta = SCENARIOS[key].build(Simulator(seed=SEED), START, DAYS)
    stream = [normalise(event.to_record()) for event in events]
    target = date.fromisoformat(meta["target_day"])
    baseline = learn(stream, roster(), until=at(target, 0))
    return stream, baseline, target, meta


def test_expectations_hold():
    section("every scenario behaves as it promises")
    for key, scenario in SCENARIOS.items():
        stream, baseline, target, _ = prepared(key)
        # Every five minutes, as the service does. A false alarm that lasts
        # five minutes still wakes someone up.
        readings = walk(stream, baseline, roster(), at(target, 0), at(target, 23, 55), 5)
        states = {reading.state for reading in readings}
        worst = peak(readings)

        if scenario.expectation == EXPECT_ALERT:
            check("%s reaches an alert" % key, ALERT in states,
                  "peak was %s" % (worst.state if worst else "none"))
        else:
            check("%s never reaches an alert" % key, ALERT not in states,
                  "peak was %s" % (worst.state if worst else "none"))


def test_the_contrast():
    """The same silence, judged differently, because of one door event."""
    section("collapse and outing at the same moment")
    fall_stream, fall_base, fall_day, _ = prepared("fall")
    away_stream, away_base, away_day, _ = prepared("away")
    people = roster()

    moment_fall = at(fall_day, 12)
    moment_away = at(away_day, 12)
    collapsed = assess(fall_stream, fall_base, people, moment_fall)
    out = assess(away_stream, away_base, people, moment_away)

    check("the collapse is an alert", collapsed.state == ALERT, collapsed.state)
    check("the outing is not on the ladder at all", out.state == AWAY, out.state)
    check("the outing has been quiet for longer than the collapse",
          out.silence_seconds > collapsed.silence_seconds,
          "outing %.0fs, collapse %.0fs" % (out.silence_seconds, collapsed.silence_seconds))
    check("a door event explains the outing", out.departure_at is not None)
    check("no door event explains the collapse", collapsed.departure_at is None)
    check("the collapse says it is a collapse",
          any("at home" in reason for reason in collapsed.reasons))


def test_away_comes_back_down():
    section("the ladder recovers")
    stream, baseline, target, meta = prepared("away")
    readings = walk(stream, baseline, roster(), at(target, 0), at(target, 23), 15)
    states = [reading.state for reading in readings]

    check("she is judged away during the outing", AWAY in states)
    check("the day ends normal", states[-1] == NORMAL, states[-1])
    check("she is never judged unresponsive", ALERT not in states and CONCERN not in states,
          ",".join(sorted(set(states))))

    moved = changes(readings)
    check("the log holds only the moves", len(moved) < len(readings) / 3, str(len(moved)))
    check("every state change is a real change",
          all(a.state != b.state for a, b in zip(moved, moved[1:])))


def test_a_caller_does_not_clear_an_alert():
    """Somebody at the door is not somebody getting up."""
    section("a caller while she is unresponsive")
    stream, baseline, target, meta = prepared("fall")
    people = roster()
    collapse = datetime.fromisoformat(meta["stops_at"])
    moment = collapse + timedelta(hours=4)

    before = assess(stream, baseline, people, moment)
    check("the collapse is an alert before anyone calls", before.state == ALERT, before.state)

    caller = collapse + timedelta(hours=3)
    visited = sorted(
        stream + [
            Event("visit_motion", "front_door", "motion", caller),
            Event("visit_ding", "front_door", "ding", caller + timedelta(seconds=20)),
        ],
        key=lambda event: event.at,
    )

    after = assess(visited, baseline, people, moment)
    check("a doorbell press does not clear the alert", after.state == ALERT, after.state)
    check("the caller is not mistaken for a departure", after.departure_at is None,
          str(after.departure_at))
    check("the silence is unchanged by the caller",
          abs(after.silence_seconds - before.silence_seconds) < 1.0)


def test_visitor_at_an_empty_house():
    section("a caller while she is out")
    stream, baseline, target, meta = prepared("visitor_while_out")
    readings = walk(stream, baseline, roster(), at(target, 0), at(target, 23), 15)
    states = [reading.state for reading in readings]
    check("the house is judged away, not unresponsive",
          AWAY in states and ALERT not in states, ",".join(sorted(set(states))))
    check("she is home and normal by the end", states[-1] == NORMAL, states[-1])


def test_sleeping_in_is_not_an_emergency():
    section("a long lie in")
    stream, baseline, target, meta = prepared("long_sleep")
    people = roster()
    wakes = datetime.fromisoformat(meta["wakes_at"])

    readings = walk(stream, baseline, people, at(target, 0), wakes, 15)
    states = {reading.state for reading in readings}
    check("a lie in never alerts", ALERT not in states, ",".join(sorted(states)))
    check("a lie in is noticed on the dashboard", QUIET in states or CONCERN in states,
          ",".join(sorted(states)))

    after = assess(stream, baseline, people, wakes + timedelta(hours=1))
    check("things are normal once she is up", after.state == NORMAL, after.state)


def test_a_broken_camera_is_not_an_emergency():
    section("one camera down")
    stream, baseline, target, meta = prepared("camera_offline")
    people = roster()
    device_id = meta["offline_device"]
    start = datetime.fromisoformat(meta["offline_from"])
    end = datetime.fromisoformat(meta["offline_until"])

    readings = walk(stream, baseline, people, start, end, 15)
    states = {reading.state for reading in readings}
    check("a broken camera never causes an alert", ALERT not in states, ",".join(sorted(states)))

    noticing = [r for r in readings if device_id in r.devices_down]
    check("the outage is noticed", noticing)
    check("the outage is named in the reasoning",
          any("Kitchen" in " ".join(r.reasons) for r in noticing))

def _with_outage(stream, device_id, start):
    return sorted(
        stream + [Event("forced_off", device_id, "device_offline", start)],
        key=lambda event: event.at,
    )


def test_a_dead_camera_in_a_busy_room_holds_back_the_alarm():
    section("a dead camera where she would normally be")
    stream, baseline, target, meta = prepared("fall")
    people = roster()
    collapse = datetime.fromisoformat(meta["stops_at"])
    moment = collapse + timedelta(hours=3)

    plain = assess(stream, baseline, people, moment)
    check("the collapse alerts while every camera works", plain.state == ALERT, plain.state)

    busy = [
        device_id for device_id in people.interior_ids()
        if baseline.activity_rate(device_id, "weekend" if target.weekday() >= 5 else "weekday",
                                  moment.hour) >= 0.5
    ]
    check("there is a room she is normally active in at this hour", busy, str(moment.hour))

    blinded = assess(_with_outage(stream, busy[0], collapse - timedelta(minutes=10)),
                     baseline, people, moment)
    check("the alarm is held back to concern", blinded.state == CONCERN, blinded.state)
    check("holding back is recorded", blinded.capped_by_outage)
    check("the reasoning names the camera to check",
          "Worth checking" in " ".join(blinded.reasons), " ".join(blinded.reasons))


def test_a_dead_camera_in_an_unused_room_does_not_suppress_the_alarm():
    """Otherwise one flaky camera could switch off alerting for the house."""
    section("a dead camera where she rarely is")
    stream, baseline, target, meta = prepared("fall")
    people = roster()
    collapse = datetime.fromisoformat(meta["stops_at"])
    moment = collapse + timedelta(hours=3)
    daytype = "weekend" if target.weekday() >= 5 else "weekday"

    idle = [
        device_id for device_id in people.interior_ids()
        if baseline.activity_rate(device_id, daytype, moment.hour) < 0.5
    ]
    check("there is a room she rarely uses at this hour", idle, str(moment.hour))

    if idle:
        blinded = assess(_with_outage(stream, idle[0], collapse - timedelta(minutes=10)),
                         baseline, people, moment)
        check("the alert still stands", blinded.state == ALERT, blinded.state)
        check("nothing was held back", not blinded.capped_by_outage)
        check("the dead camera is still reported", idle[0] in blinded.devices_down)


def test_a_blind_house_says_so():
    section("every camera down")
    people = roster()
    base = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
    events = [Event("seed", "kitchen", "motion", base)]
    for offset, device_id in enumerate(people.interior_ids()):
        events.append(Event("off%d" % offset, device_id, "device_offline",
                            base + timedelta(minutes=1)))

    reading = assess(events, Baseline(), people, base + timedelta(hours=2))
    check("a blind house cannot be judged", reading.state == UNKNOWN, reading.state)
    check("a blind house does not claim to be normal", reading.state != NORMAL)
    check("every offline camera is listed",
          set(reading.devices_down) == set(people.interior_ids()))
    check("the headline says we cannot tell", "Cannot tell" in reading.headline,
          reading.headline)


def test_no_history_says_so():
    section("nothing learned yet")
    people = roster()
    base = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)

    empty = assess([], Baseline(), people, base)
    check("no events at all cannot be judged", empty.state == UNKNOWN, empty.state)

    seeded = [Event("one", "kitchen", "motion", base)]
    unlearned = assess(seeded, Baseline(), people, base + timedelta(hours=3))
    check("events without a baseline cannot be judged", unlearned.state == UNKNOWN,
          unlearned.state)
    check("the reason names the missing baseline",
          any("baseline" in reason for reason in unlearned.reasons),
          " ".join(unlearned.reasons))


def test_every_reading_carries_its_reasoning():
    section("nothing is asserted without a reason")
    stream, baseline, target, _ = prepared("fall")
    readings = walk(stream, baseline, roster(), at(target, 0), at(target, 23), 30)

    check("every reading has a headline", all(r.headline for r in readings))
    check("every reading has at least one reason", all(r.reasons for r in readings))
    check("headlines are sentences", all(r.headline.endswith(".") for r in readings))

    for reading in readings:
        if reading.state in (CONCERN, ALERT):
            check("the %s at %s says how long and how long is usual"
                  % (reading.state, reading.at.strftime("%H:%M")),
                  reading.silence_seconds is not None and reading.threshold_seconds is not None)
            break

    check("readings serialise for a dashboard",
          json.loads(json.dumps([r.to_dict() for r in readings[:5]])))


def test_ladder_helpers():
    section("ladder helpers")
    stream, baseline, target, _ = prepared("fall")
    readings = walk(stream, baseline, roster(), at(target, 0), at(target, 23), 15)

    worst = peak(readings)
    check("the peak is the alert", worst.state == ALERT, worst.state)
    highest = [r for r in readings if r.state == ALERT]
    check("the peak is the latest reading at that rung", worst.at == highest[-1].at)
    check("normal sits below alert",
          assess(stream, baseline, roster(), at(target, 1)).rung < worst.rung)

    away_stream, away_base, away_day, _ = prepared("away")
    out = assess(away_stream, away_base, roster(), at(away_day, 12))
    check("away sits beside the ladder rather than on it", out.rung is None, out.state)
    check("away is not treated as needing attention", not out.needs_attention)


def main():
    for test in (
        test_expectations_hold,
        test_the_contrast,
        test_away_comes_back_down,
        test_a_caller_does_not_clear_an_alert,
        test_visitor_at_an_empty_house,
        test_sleeping_in_is_not_an_emergency,
        test_a_broken_camera_is_not_an_emergency,
        test_a_dead_camera_in_a_busy_room_holds_back_the_alarm,
        test_a_dead_camera_in_an_unused_room_does_not_suppress_the_alarm,
        test_a_blind_house_says_so,
        test_no_history_says_so,
        test_every_reading_carries_its_reasoning,
        test_ladder_helpers,
    ):
        test()

    print("\n%d checks passed, %d failed" % (PASSED, len(FAILED)))
    if FAILED:
        for label in FAILED:
            print("  failed: %s" % label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
