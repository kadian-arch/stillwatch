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
    SETTLED,
    UNKNOWN,
    assess,
    changes,
    peak,
    walk,
    when_text,
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
    # Said in words a family would use, not in the engine's. "No baseline has
    # been learned for a weekday at 08:00" is accurate and means nothing to
    # the person reading it at the time it matters.
    check("the reason explains the gap without naming the machinery",
          any("not watched enough" in reason for reason in unlearned.reasons)
          and not any("baseline" in reason for reason in unlearned.reasons),
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


def test_an_answer_settles_it():
    section("somebody looks, and the question stops being asked")
    stream, baseline, target, _ = prepared("fall")
    readings = walk(stream, baseline, roster(), at(target, 0), at(target, 23, 55), 5)
    worst = peak(readings)
    check("the fall still reaches an alert", worst.state == ALERT)

    episode = worst.silence_began.isoformat()
    answered = {episode: {"episode": episode, "outcome": "fine", "by": "Lucie",
                          "at": at(target, 14, 30).isoformat()}}

    after = assess(stream, baseline, roster(), worst.at, answered=answered)
    check("the state becomes settled", after.state == SETTLED, after.state)
    check("it says who looked", "Lucie" in after.headline, after.headline)
    check("it stops needing attention", not after.needs_attention)
    check("the reasoning is still there, not thrown away",
          any("Last movement was in the" in reason for reason in after.reasons),
          str(after.reasons))
    check("and the engine still knows what it found",
          after.silence_seconds == worst.silence_seconds)

    other = assess(stream, baseline, roster(), worst.at,
                   answered={"2020-01-01T00:00:00+00:00": {"outcome": "fine"}})
    check("an answer about a different stretch changes nothing",
          other.state == ALERT, other.state)

    check("a quiet house with no answer is untouched",
          assess(stream, baseline, roster(), worst.at).state == ALERT)


def test_two_people_in_the_house_is_said_out_loud():
    section("two rooms at once is two people, and it says so")
    stream, baseline, target, _ = prepared("fall")
    worst = peak(walk(stream, baseline, roster(), at(target, 0), at(target, 23, 55), 5))

    plain = assess(stream, baseline, roster(), worst.at)

    # The landing camera sees her every time she steps out of a bedroom, so
    # the landing and the bedroom move together constantly for one person
    # living alone. The baseline works out which cameras do that before any of
    # this is allowed to mean a second person.
    check("the passageway camera is learned, not named",
          baseline.passages == ["landing"], str(baseline.passages))
    check("nothing is claimed when she was alone in 28 days of history",
          plain.company_at is None, str(plain.company_at))

    # Somebody else moving in a different room, seconds after her last
    # movement. Nobody is in two rooms at once, and neither room is the way
    # between them, so this can only be a second person.
    began = plain.silence_began
    hers = [event.device_id for event in stream if event.at == began]
    elsewhere = [device_id for device_id in roster().interior_ids()
                 if device_id not in hers + baseline.passages][0]
    visitor = normalise({
        "event_id": "visitor-1", "device_id": elsewhere, "kind": "motion",
        "created_at": (began - timedelta(seconds=4)).isoformat(),
    })
    check("the visitor is in a different room from her last movement",
          elsewhere not in hers, "%s vs %s" % (elsewhere, hers))

    shared = assess(stream + [visitor], baseline, roster(), worst.at)
    check("company is spotted", shared.company_at is not None)
    check("and it is on the page, not buried",
          any("somebody else was in the house" in reason for reason in shared.reasons),
          str(shared.reasons))
    check("it does not quietly cancel the alarm", shared.state == ALERT, shared.state)


def test_an_unanswered_doorbell_is_worth_saying():
    section("the bell rang and nothing moved")
    stream, baseline, target, _ = prepared("fall")
    worst = peak(walk(stream, baseline, roster(), at(target, 0), at(target, 23, 55), 5))
    began = worst.silence_began

    caller = normalise({
        "event_id": "caller-1", "device_id": "front_door", "kind": "ding",
        "created_at": (began + timedelta(hours=1)).isoformat(),
    })
    reading = assess(stream + [caller], baseline, roster(), worst.at)
    check("the call is noticed", reading.unanswered_ding_at is not None)
    check("and explained", any("rang the doorbell" in reason for reason in reading.reasons),
          str(reading.reasons))


def test_a_time_says_which_day_it_belongs_to():
    section("yesterday is not just a clock reading")
    now = at(date(2026, 10, 1), 10, 30)
    check("today is a plain time", when_text(at(date(2026, 10, 1), 7, 5), now) == "07:05")
    check("yesterday says so",
          when_text(at(date(2026, 9, 30), 10, 32), now) == "10:32 yesterday",
          when_text(at(date(2026, 9, 30), 10, 32), now))
    check("earlier in the week names the day",
          when_text(at(date(2026, 9, 28), 9, 0), now) == "09:00 on Monday",
          when_text(at(date(2026, 9, 28), 9, 0), now))

    stream, baseline, target, _ = prepared("fall")
    worst = peak(walk(stream, baseline, roster(), at(target, 0), at(target, 23, 55), 5))
    tomorrow = assess(stream, baseline, roster(), at(target + timedelta(days=1), 10))
    check("a silence that ran overnight is not reported as this morning",
          "yesterday" in tomorrow.headline, tomorrow.headline)
    check("and it never reads as a bare clock time against a different day",
          worst.headline != tomorrow.headline)


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


def test_reasons_read_like_written_english():
    section("the reasons on the card are proof read")

    from stillwatch.monitor import sentence

    # str.capitalize lowercases everything after the first letter, which put
    # "is 4h 39m. that is measured from 144 times" on the live dashboard.
    two = "the longest she normally stays still is 4h 39m. That is measured from 144 times"
    check("a second sentence keeps its capital", sentence(two) ==
          "The longest she normally stays still is 4h 39m. That is measured from 144 times",
          sentence(two))
    check("a room name is not flattened",
          sentence("movement was in the Landing") == "Movement was in the Landing",
          sentence("movement was in the Landing"))
    check("an empty string is left alone", sentence("") == "")


def test_an_ordinary_day_is_never_worth_a_phone_call():
    section("nothing wrong at all")

    # Every other scenario had a test and this one did not, which is the wrong
    # way round. A product that telephones a family about their mother is
    # judged first on the days she is perfectly fine, and those are almost all
    # the days there are. One missed collapse is a failure. One false alarm a
    # month is the reason the product gets switched off.
    stream, baseline, target, meta = prepared("normal")
    people = roster()

    readings = walk(stream, baseline, people, at(target, 0), at(target, 23, 59), 10)
    alerts = [r.at.strftime("%H:%M") for r in readings if r.state == ALERT]
    concerns = [r.at.strftime("%H:%M") for r in readings if r.state == CONCERN]

    check("an ordinary day never asks anybody to go and check",
          not alerts, "%d: %s" % (len(alerts), ", ".join(alerts[:6])))
    check("and never even says it is worried",
          not concerns, "%d: %s" % (len(concerns), ", ".join(concerns[:6])))

    # Silent is not the same as asleep.
    midday = assess(stream, baseline, people, at(target, 12))
    check("while still judging, rather than quiet because it gave up",
          midday.state in (NORMAL, QUIET, AWAY), midday.state)
    check("and able to say why it is not worried", bool(midday.reasons))


def test_how_often_an_ordinary_day_cries_wolf():
    section("the same ordinary day, six different households")

    # One household proves nothing about the rule. These are six, each with
    # its own routine, each judged on its ordinary day by a baseline that has
    # not seen that day, which is what the deployed service does.
    #
    # Five are silent. One is not, and that is recorded here rather than
    # tuned away, because the number that matters is how often this happens
    # across households and it cannot be learned from the one that passes.
    from ringsim import Simulator
    from ringsim.scenarios import SCENARIOS

    people = roster()
    noisy = []
    for seed in (3, 7, 11, 19, 23, 31):
        events, meta = SCENARIOS["normal"].build(Simulator(seed=seed), START, DAYS)
        stream = [normalise(event.to_record()) for event in events]
        day = date.fromisoformat(meta["target_day"])
        learned = learn(stream, roster(), until=at(day, 0))
        readings = walk(stream, learned, people, at(day, 0), at(day, 23, 59), 10)
        if any(r.state == ALERT for r in readings):
            noisy.append(seed)

    check("at most one household in six is alarmed on an ordinary day",
          len(noisy) <= 1, "%d of 6 were: %s" % (len(noisy), noisy))


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
        test_an_answer_settles_it,
        test_two_people_in_the_house_is_said_out_loud,
        test_an_unanswered_doorbell_is_worth_saying,
        test_a_time_says_which_day_it_belongs_to,
        test_ladder_helpers,
        test_an_ordinary_day_is_never_worth_a_phone_call,
        test_how_often_an_ordinary_day_cries_wolf,
        test_reasons_read_like_written_english,
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
