"""Checks on the rhythm learner. Run directly, exits non zero on failure."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "ring-event-simulator"))

from ringsim import Simulator
from ringsim.devices import DEFAULT_DEVICES, manifest
from ringsim.scenarios import SCENARIOS

from stillwatch.model import (
    Device,
    DeviceRoster,
    Event,
    is_down,
    normalise,
    outage_spans,
    parse_timestamp,
)
from stillwatch.rhythm import (
    ANY_INTERIOR,
    WEEKDAY,
    WEEKEND,
    Baseline,
    daytype_of,
    human_duration,
    learn,
    median_absolute_deviation,
    percentile,
)

START = date(2026, 8, 24)
DAYS = 28
SEED = 7

DAY_HOURS = (9, 11, 13, 15, 17, 19)
NIGHT_HOURS = (0, 1, 2, 3, 23)

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


def stream(key="normal", days=DAYS, seed=SEED):
    events, meta = SCENARIOS[key].build(Simulator(seed=seed), START, days)
    return [normalise(event.to_record()) for event in events], meta


def test_model():
    section("event and device model")
    moment = parse_timestamp("2026-09-20T07:41:09Z")
    check("a Z suffix parses as UTC", moment.tzinfo is not None and moment.hour == 7)
    check("an offset suffix parses", parse_timestamp("2026-09-20T07:41:09+00:00").minute == 41)

    people = roster()
    check("doors are transit", people.is_transit("front_door") and people.is_transit("back_door"))
    check("rooms are interior", people.is_interior("kitchen") and people.is_interior("living_room"))
    check("interior and transit partition the roster",
          len(people.interior_ids()) + len(people.transit_ids()) == len(people))

    event = normalise({"event_id": "e1", "device_id": "kitchen", "kind": "motion",
                       "created_at": "2026-09-20T07:30:00+00:00"})
    check("minute of day is computed", abs(event.minute_of_day - 450.0) < 0.01,
          str(event.minute_of_day))


def test_outage_tracking():
    section("device outage tracking")
    base = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    events = [
        Event("a", "kitchen", "device_offline", base),
        Event("b", "kitchen", "device_online", base + timedelta(hours=3)),
        Event("c", "hallway", "device_offline", base + timedelta(hours=5)),
    ]
    spans = outage_spans(events)
    check("a closed outage is recorded", ("kitchen", base, base + timedelta(hours=3)) in spans)
    check("an outage still open has no end",
          any(d == "hallway" and e is None for d, _, e in spans))

    check("a device is down inside its outage",
          is_down(spans, "kitchen", base + timedelta(hours=1), base + timedelta(hours=2)))
    check("a device is not down before its outage",
          not is_down(spans, "kitchen", base - timedelta(hours=2), base - timedelta(hours=1)))
    check("a device is not down after its outage",
          not is_down(spans, "kitchen", base + timedelta(hours=4), base + timedelta(hours=5)))
    check("an overlapping window counts as down",
          is_down(spans, "kitchen", base + timedelta(hours=2), base + timedelta(hours=6)))


def test_statistics():
    section("statistics helpers")
    check("percentile of one value", percentile([5.0], 0.95) == 5.0)
    check("percentile interpolates", abs(percentile([0.0, 10.0], 0.5) - 5.0) < 1e-9)
    check("percentile of nothing is None", percentile([], 0.5) is None)
    check("deviation of a constant run is zero",
          median_absolute_deviation([4.0, 4.0, 4.0]) == 0.0)
    check("durations read as text", human_duration(5400) == "1h 30m", human_duration(5400))
    check("whole hours drop the minutes", human_duration(7200) == "2h")

    monday = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc)
    check("weekdays and weekends are told apart",
          daytype_of(monday) == WEEKDAY and daytype_of(saturday) == WEEKEND)


def test_baseline_shape():
    section("baseline from ordinary weeks")
    events, _ = stream()
    baseline = learn(events, roster())

    total = sum(baseline.days_observed.values())
    covered = {event.at.date() for event in events}
    check("partial first and last days are left out", total == len(covered) - 2,
          "%d full of %d touched" % (total, len(covered)))
    check("both kinds of day are represented",
          baseline.days_observed[WEEKDAY] > 0 and baseline.days_observed[WEEKEND] > 0)
    check("the span is recorded", baseline.first_event and baseline.last_event)

    rate = baseline.activity_rate("kitchen", WEEKDAY, 8)
    check("the kitchen is busy on a weekday morning", rate > 0.5, "%.2f" % rate)
    rate = baseline.activity_rate("kitchen", WEEKDAY, 3)
    check("the kitchen is idle at three in the morning", rate < 0.1, "%.2f" % rate)


def test_night_is_more_tolerant():
    section("the day and night difference is learned, not written down")
    events, _ = stream()
    baseline = learn(events, roster())

    day = [baseline.tolerated_quiet(WEEKDAY, hour) for hour in DAY_HOURS]
    night = [baseline.tolerated_quiet(WEEKDAY, hour) for hour in NIGHT_HOURS]
    check("every hour has a baseline", all(v is not None for v in day + night))

    worst_day = max(v for v in day if v is not None)
    best_night = min(v for v in night if v is not None)
    check("night tolerates far longer silence than day",
          best_night > worst_day * 2,
          "night %s, day %s" % (human_duration(best_night), human_duration(worst_day)))


def test_daytime_threshold_is_usable():
    section("daytime thresholds are actionable")
    events, _ = stream()
    baseline = learn(events, roster())
    for hour in DAY_HOURS:
        seconds = baseline.tolerated_quiet(WEEKDAY, hour)
        check("a silence starting at %02d:00 has a usable threshold" % hour,
              seconds is not None and 600 <= seconds <= 7200,
              human_duration(seconds))


def test_absences_are_excluded():
    section("time out of the house does not inflate the baseline")
    events, _ = stream("away")
    people = roster()

    kept = learn(events, people)
    check("departures were recognised and set aside", kept.excluded_absences > 0,
          str(kept.excluded_absences))

    naive = learn(events, people, departure_window=(0, 0))
    check("without the rule nothing is set aside", naive.excluded_absences == 0)

    inflated = []
    for hour in DAY_HOURS:
        careful = kept.tolerated_quiet(WEEKDAY, hour)
        sloppy = naive.tolerated_quiet(WEEKDAY, hour)
        if careful is not None and sloppy is not None and sloppy > careful * 1.5:
            inflated.append(hour)
    check("ignoring departures would raise daytime thresholds", inflated,
          "this is why the rule exists")

    for hour in DAY_HOURS:
        seconds = kept.tolerated_quiet(WEEKDAY, hour)
        check("daytime threshold at %02d:00 stays tight despite an outing" % hour,
              seconds is not None and seconds <= 7200, human_duration(seconds))


def test_anchors():
    section("anchors")
    events, _ = stream()
    baseline = learn(events, roster())
    check("anchors were found", baseline.anchors)

    check("no anchor wanders more than the cap",
          all(anchor.spread_minutes <= 45.0 for anchor in baseline.anchors))
    check("every anchor holds on most days",
          all(anchor.hit_rate >= 0.7 for anchor in baseline.anchors))

    mornings = [a for a in baseline.anchors if a.window == "morning"]
    check("the morning has an anchor", mornings)
    check("a whole house morning anchor exists",
          any(a.device_id == ANY_INTERIOR for a in mornings))

    for anchor in mornings:
        check("the %s morning anchor sits in the morning" % anchor.daytype,
              300 <= anchor.median_minute < 660, anchor.clock())

    rise = [a for a in mornings if a.device_id == ANY_INTERIOR and a.daytype == WEEKDAY][0]
    on_time = rise.late_by(rise.median_minute)
    overdue = rise.late_by(rise.median_minute + 5 * max(rise.spread_minutes, 10.0))
    check("an anchor is not late at its usual time", on_time < 0, "%.1f" % on_time)
    check("an anchor is late once well past it", overdue > 0, "%.1f" % overdue)


def test_anchors_are_onsets():
    """Being busy across a window boundary is not a habit of arriving then."""
    section("a habit is when something starts")
    people = roster()
    base = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)

    events = []
    counter = 0
    for offset in range(14):
        day = base + timedelta(days=offset)
        for minute in range(9 * 60, 13 * 60, 10):
            counter += 1
            events.append(Event("s%d" % counter, "living_room", "motion",
                                day + timedelta(minutes=minute)))
        for minute in range(12 * 60 + 2, 12 * 60 + 40, 8):
            counter += 1
            events.append(Event("k%d" % counter, "kitchen", "motion",
                                day + timedelta(minutes=minute)))
    events.sort(key=lambda event: event.at)

    baseline = learn(events, people)
    sitting = [a for a in baseline.anchors if a.device_id == "living_room" and a.window == "midday"]
    check("sitting through eleven o'clock is not an anchor", not sitting,
          ", ".join(a.clock() for a in sitting))

    lunch = [a for a in baseline.anchors
             if a.device_id == "kitchen" and a.window == "midday" and a.daytype == WEEKDAY]
    check("starting lunch at noon is an anchor", lunch)
    if lunch:
        check("the lunch anchor sits at noon", abs(lunch[0].median_minute - 722) < 5, lunch[0].clock())


def test_outage_does_not_count_as_stillness():
    section("a dead camera is not a quiet room")
    people = roster()
    base = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)

    events = []
    counter = 0
    for offset in range(14):
        day = base + timedelta(days=offset)
        for hour in range(7, 21):
            counter += 1
            events.append(Event("e%d" % counter, "kitchen", "motion",
                                day + timedelta(hours=hour, minutes=5)))
            counter += 1
            events.append(Event("e%d" % counter, "living_room", "motion",
                                day + timedelta(hours=hour, minutes=25)))

    without = learn(events, people)
    rate = without.activity_rate("kitchen", WEEKDAY, 9)
    check("a device seen every day scores full marks", rate > 0.99, "%.2f" % rate)

    broken = list(events)
    outage_day = base + timedelta(days=3)
    check("the outage lands on a weekday, as this check assumes",
          daytype_of(outage_day) == WEEKDAY, outage_day.isoformat())
    broken = [e for e in broken
              if not (e.device_id == "kitchen"
                      and outage_day <= e.at < outage_day + timedelta(days=1))]
    broken.append(Event("off", "kitchen", "device_offline", outage_day))
    broken.append(Event("on", "kitchen", "device_online", outage_day + timedelta(days=1)))
    broken.sort(key=lambda event: event.at)

    repaired = learn(broken, people)
    rate = repaired.activity_rate("kitchen", WEEKDAY, 9)
    check("a day the camera was down is not counted against it", rate > 0.99, "%.2f" % rate)
    check("that day is dropped from the denominator",
          repaired.activity_days("kitchen", WEEKDAY, 9)
          < without.activity_days("kitchen", WEEKDAY, 9))
    check("other devices keep their full history",
          repaired.activity_days("living_room", WEEKDAY, 9)
          == without.activity_days("living_room", WEEKDAY, 9))


def test_until_cuts_history():
    section("learning up to a moment")
    events, _ = stream()
    people = roster()
    everything = learn(events, people)

    cutoff = events[-1].at - timedelta(days=6)
    partial = learn(events, people, until=cutoff)
    check("a cutoff shortens the history",
          sum(partial.days_observed.values()) < sum(everything.days_observed.values()))
    check("nothing after the cutoff is used",
          partial.last_event < cutoff.isoformat(), partial.last_event)
    check("a shortened history still learns", partial.anchors and partial.quiet)


def test_round_trip():
    section("saving and loading")
    events, _ = stream()
    baseline = learn(events, roster())
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "baseline.json"
        baseline.save(path)
        check("the file is valid json", json.loads(path.read_text(encoding="utf-8")))
        again = Baseline.load(path)

    check("day counts survive", again.days_observed == baseline.days_observed)
    check("anchors survive", len(again.anchors) == len(baseline.anchors))
    check("thresholds survive",
          again.tolerated_quiet(WEEKDAY, 14) == baseline.tolerated_quiet(WEEKDAY, 14))
    check("the excluded count survives", again.excluded_absences == baseline.excluded_absences)
    check("anchors are usable after loading",
          all(hasattr(anchor, "clock") for anchor in again.anchors))


def test_empty_history():
    section("no history at all")
    baseline = learn([], roster())
    check("an empty history does not raise", isinstance(baseline, Baseline))
    check("an empty history has no thresholds", baseline.tolerated_quiet(WEEKDAY, 12) is None)
    check("an empty history reads as unknown",
          "no baseline" in baseline.describe_quiet(WEEKDAY, 12))


def main():
    for test in (
        test_model,
        test_outage_tracking,
        test_statistics,
        test_baseline_shape,
        test_night_is_more_tolerant,
        test_daytime_threshold_is_usable,
        test_absences_are_excluded,
        test_anchors,
        test_anchors_are_onsets,
        test_outage_does_not_count_as_stillness,
        test_until_cuts_history,
        test_round_trip,
        test_empty_history,
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
