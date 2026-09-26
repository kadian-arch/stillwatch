"""Checks that the engine reads the household's clock, not the server's.

Every judgement is about a person's day. A silence beginning at 23:00 is a
night silence, and the threshold that explains it is looked up by that hour.
If the engine read hours in UTC while the home sat an hour away, every learned
threshold would be off by one and the wrong quiet would look normal.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["STILLWATCH_TZ"] = "UTC"

from stillwatch.clock import (
    clock,
    household_tz,
    local_date,
    local_hour,
    midnight_on,
    offset_minutes,
)
from stillwatch.model import Device, DeviceRoster, Event, MOTION
from stillwatch.rhythm import WEEKDAY, WEEKEND, daytype_of, learn

PASSED = 0
FAILED = []

DOUALA = "Africa/Douala"          # UTC+1, no daylight saving
CHICAGO = "America/Chicago"       # behind UTC, so the day can roll backwards


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


def using(name):
    os.environ["STILLWATCH_TZ"] = name


def roster():
    return DeviceRoster([Device("kitchen", "Kitchen", "interior")])


def test_reading_the_clock():
    section("reading the clock")

    # Friday night in UTC is already Saturday morning one hour east.
    late = datetime(2026, 9, 25, 23, 30, tzinfo=timezone.utc)

    using("UTC")
    check("UTC shows the stored hour", clock(late) == "23:30", clock(late))
    check("and the stored date", local_date(late).isoformat() == "2026-09-25")
    check("Friday is a weekday", daytype_of(late) == WEEKDAY)

    using(DOUALA)
    check("an hour east it is past midnight", clock(late) == "00:30", clock(late))
    check("so the date has rolled over", local_date(late).isoformat() == "2026-09-26",
          local_date(late).isoformat())
    check("and it is now the weekend", daytype_of(late) == WEEKEND)
    check("the hour is read locally", local_hour(late) == 0, str(local_hour(late)))

    using(CHICAGO)
    check("west of UTC the day rolls back", local_date(late).isoformat() == "2026-09-25")
    check("and the clock reads earlier", clock(late) == "18:30", clock(late))

    using("Not/ARealZone")
    check("an unknown zone falls back to UTC rather than guessing",
          clock(late) == "23:30", clock(late))

    using(DOUALA)
    check("the offset is reported for a visitor", offset_minutes(late) == 60,
          str(offset_minutes(late)))
    check("midnight is local midnight",
          midnight_on(local_date(late)).utcoffset() == timedelta(hours=1))
    check("which is not UTC midnight",
          midnight_on(local_date(late)).astimezone(timezone.utc).hour == 23)


def test_minute_of_day():
    section("where an event sits in the day")

    late = Event("e1", "kitchen", MOTION, datetime(2026, 9, 25, 23, 30, tzinfo=timezone.utc))

    using("UTC")
    check("UTC puts it at 23:30", round(late.minute_of_day) == 23 * 60 + 30,
          str(round(late.minute_of_day)))

    using(DOUALA)
    check("an hour east it is half past midnight", round(late.minute_of_day) == 30,
          str(round(late.minute_of_day)))


def test_the_baseline_moves_with_the_household():
    section("the rhythm is learned on the household's hours")

    # The same person, moving once a night at 23:30 UTC, for three weeks.
    start = datetime(2026, 9, 1, 23, 30, tzinfo=timezone.utc)
    events = [
        Event("e%d" % index, "kitchen", MOTION, start + timedelta(days=index))
        for index in range(21)
    ]
    until = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)

    using("UTC")
    in_utc = learn(events, roster(), until=until)
    late_utc = max(in_utc.activity_rate("kitchen", day, 23) for day in (WEEKDAY, WEEKEND))
    early_utc = max(in_utc.activity_rate("kitchen", day, 0) for day in (WEEKDAY, WEEKEND))

    using(DOUALA)
    in_douala = learn(events, roster(), until=until)
    late_douala = max(in_douala.activity_rate("kitchen", day, 23) for day in (WEEKDAY, WEEKEND))
    early_douala = max(in_douala.activity_rate("kitchen", day, 0) for day in (WEEKDAY, WEEKEND))

    check("in UTC she is active at 23", late_utc > 0, str(late_utc))
    check("and never at midnight", early_utc == 0, str(early_utc))
    check("an hour east she is active at midnight", early_douala > 0, str(early_douala))
    check("and never at 23", late_douala == 0, str(late_douala))


def main():
    try:
        for test in (test_reading_the_clock, test_minute_of_day,
                     test_the_baseline_moves_with_the_household):
            test()
    finally:
        using("UTC")

    print("\n%d checks passed, %d failed" % (PASSED, len(FAILED)))
    if FAILED:
        for label in FAILED:
            print("  failed: %s" % label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
