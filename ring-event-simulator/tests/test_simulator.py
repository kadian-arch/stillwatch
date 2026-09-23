"""Checks on the generated streams. Run directly, exits non zero on failure."""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ringsim import Simulator
from ringsim.devices import DEFAULT_DEVICES, INTERIOR, TRANSIT, by_id
from ringsim.events import DING, MOTION, OFFLINE, ONLINE
from ringsim.scenarios import SCENARIOS

START = date(2026, 8, 31)
DAYS = 21
TARGET = START + timedelta(days=DAYS - 1)
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


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=timezone.utc)


def run(key):
    simulator = Simulator(seed=SEED)
    return SCENARIOS[key].build(simulator, START, DAYS)


def zone_class(device_id):
    return by_id(DEFAULT_DEVICES)[device_id].zone_class


def interior(events):
    return [e for e in events if e.kind == MOTION and zone_class(e.device_id) == INTERIOR]


def transit(events):
    return [e for e in events if e.kind in (MOTION, DING) and zone_class(e.device_id) == TRANSIT]


def last_before(events, moment):
    earlier = [e for e in events if e.created_at <= moment]
    return earlier[-1] if earlier else None


def section(title):
    print("\n%s" % title)


def test_structure():
    section("stream structure")
    events, _ = run("normal")

    stamps = [e.created_at for e in events]
    check("events are in time order", stamps == sorted(stamps))

    ids = [e.event_id for e in events]
    check("event ids are unique", len(set(ids)) == len(ids))
    check("event ids are ordered", ids == sorted(ids))

    kinds = {e.kind for e in events}
    check("only known event kinds", kinds <= {MOTION, DING, OFFLINE, ONLINE}, str(kinds))

    names = {e.device_id for e in events}
    check("only known devices", names <= set(by_id(DEFAULT_DEVICES)), str(names))

    check("doorbell presses come from a ding capable device",
          all(e.device_id == "front_door" for e in events if e.kind == DING))


def test_determinism():
    section("determinism")
    first, _ = run("normal")
    second, _ = run("normal")
    check("same seed gives the same stream",
          [e.to_record() for e in first] == [e.to_record() for e in second])

    other, _ = SCENARIOS["normal"].build(Simulator(seed=SEED + 1), START, DAYS)
    check("a different seed gives a different stream",
          [e.to_record() for e in first] != [e.to_record() for e in other])


def test_cooldown():
    section("camera cooldown")
    events, _ = run("normal")
    lookup = by_id(DEFAULT_DEVICES)
    worst = None
    for device_id, device in lookup.items():
        stamps = [e.created_at for e in events if e.device_id == device_id and e.kind == MOTION]
        for before, after in zip(stamps, stamps[1:]):
            gap = (after - before).total_seconds()
            if gap < device.cooldown_s:
                worst = "%s %.1fs < %ds" % (device_id, gap, device.cooldown_s)
                break
    check("no device fires inside its own cooldown", worst is None, worst or "")


def test_normal_rhythm():
    section("ordinary weeks")
    events, meta = run("normal")
    check("normal is labelled as a no alert case", meta["expectation"] == "no_alert")

    inside = interior(events)
    missing = []
    for offset in range(1, DAYS - 1):
        day = START + timedelta(days=offset)
        morning = [e for e in inside if at(day, 6) <= e.created_at <= at(day, 11)]
        if not morning:
            missing.append(day.isoformat())
    check("every full day has morning activity", not missing, ",".join(missing))

    doors = transit(events)
    check("the front door is used across the stream", len(doors) >= DAYS // 2, str(len(doors)))

    nights = [e for e in inside if 1 <= e.created_at.hour < 5]
    check("nights are quiet but not empty", 0 < len(nights) < len(inside) * 0.08,
          "%d of %d" % (len(nights), len(inside)))


def test_fall():
    section("collapse at home")
    events, meta = run("fall")
    check("fall is labelled as an alert case", meta["expectation"] == "alert")

    cut = datetime.fromisoformat(meta["stops_at"])
    after = [e for e in events if e.created_at > cut]
    check("nothing is reported after the collapse", not after,
          after[0].created_at.isoformat() if after else "")

    last = last_before(interior(events), cut)
    check("there is interior activity right before the collapse",
          last is not None and (cut - last.created_at).total_seconds() < 3600)

    window = [e for e in transit(events) if cut - timedelta(hours=1) <= e.created_at <= cut]
    check("no door event explains the silence", not window,
          window[0].created_at.isoformat() if window else "")


def test_away():
    section("out for the day")
    events, meta = run("away")
    check("away is labelled as a no alert case", meta["expectation"] == "no_alert")

    departs = datetime.fromisoformat(meta["departs_at"])
    quiet_from = departs + timedelta(minutes=5)
    quiet_to = at(TARGET, 16)
    inside_quiet = [e for e in interior(events) if quiet_from <= e.created_at <= quiet_to]
    check("the house is silent while she is out", not inside_quiet,
          inside_quiet[0].created_at.isoformat() if inside_quiet else "")

    doors = [e for e in transit(events)
             if departs - timedelta(minutes=10) <= e.created_at <= departs + timedelta(minutes=10)]
    check("a door event opens the silence", doors, "no transit event near departure")

    resumed = [e for e in interior(events) if e.created_at > quiet_to]
    check("activity resumes after she gets back", resumed)


def test_the_hard_case():
    """The discriminator cannot be how long the silence is."""
    section("away and collapse are not separable by gap length")
    fall_events, fall_meta = run("fall")
    away_events, away_meta = run("away")
    moment = at(TARGET, 16)

    fall_last = last_before(interior(fall_events), moment)
    away_last = last_before(interior(away_events), moment)
    fall_gap = (moment - fall_last.created_at).total_seconds() / 3600.0
    away_gap = (moment - away_last.created_at).total_seconds() / 3600.0

    check("the collapse produces hours of silence", fall_gap > 5.0, "%.1fh" % fall_gap)
    check("the outing produces hours of silence", away_gap > 5.0, "%.1fh" % away_gap)
    check("the two silences are a similar length",
          abs(fall_gap - away_gap) < 3.0,
          "collapse %.1fh, outing %.1fh" % (fall_gap, away_gap))

    def door_before(events, last_interior):
        return [e for e in transit(events)
                if last_interior <= e.created_at <= last_interior + timedelta(minutes=20)]

    check("only the outing carries a door event at the start of the silence",
          not door_before(fall_events, fall_last.created_at)
          and door_before(away_events, away_last.created_at),
          "this is the signal the detector has to use")


def test_long_sleep():
    section("a long lie in")
    events, meta = run("long_sleep")
    wakes = datetime.fromisoformat(meta["wakes_at"])
    last = last_before(interior(events), wakes)
    gap = (wakes - last.created_at).total_seconds() / 3600.0
    check("the silence before waking is a long one", gap >= 5.0, "%.1fh" % gap)

    doors = [e for e in transit(events) if last.created_at <= e.created_at <= wakes]
    check("no door event during the silence", not doors,
          doors[0].created_at.isoformat() if doors else "")

    later = [e for e in interior(events) if e.created_at >= wakes]
    check("the day starts once she is up", later)
    check("the silence sits over the night hours", wakes.hour < 12, str(wakes.hour))


def test_visitor_while_out():
    section("caller at an empty house")
    events, meta = run("visitor_while_out")
    dings = [e for e in events if e.kind == DING and e.created_at.date() == TARGET]
    check("someone rings while she is out", dings)

    if dings:
        moment = dings[-1].created_at
        follow = [e for e in interior(events)
                  if moment < e.created_at <= moment + timedelta(minutes=45)]
        check("the caller does not produce interior activity", not follow,
              follow[0].device_id if follow else "")


def test_camera_offline():
    section("one camera drops out")
    events, meta = run("camera_offline")
    device_id = meta["offline_device"]
    start = datetime.fromisoformat(meta["offline_from"])
    end = datetime.fromisoformat(meta["offline_until"])

    during = [e for e in events if e.device_id == device_id and start <= e.created_at < end
              and e.kind == MOTION]
    check("the failed camera reports nothing", not during, str(len(during)))

    markers = [e.kind for e in events if e.device_id == device_id and e.kind in (OFFLINE, ONLINE)]
    check("the outage is announced and cleared", markers == [OFFLINE, ONLINE], str(markers))

    others = [e for e in interior(events)
              if e.device_id != device_id and start <= e.created_at < end]
    check("the rest of the house carries on", others,
          "device silence must not read as person silence")


def test_expectations_declared():
    section("scenario metadata")
    for key, scenario in SCENARIOS.items():
        _, meta = run(key)
        check("%s declares its expectation" % key,
              meta["expectation"] == scenario.expectation and meta["scenario"] == key)


def main():
    for test in (
        test_structure,
        test_determinism,
        test_cooldown,
        test_normal_rhythm,
        test_fall,
        test_away,
        test_the_hard_case,
        test_long_sleep,
        test_visitor_while_out,
        test_camera_offline,
        test_expectations_declared,
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
