"""Checks that the order a delivery arrives in cannot change a judgement.

Webhooks cross the open internet and arrive when they arrive. Two events a
second apart can land in either order, and a retry after an outage can land an
hour late. None of that is allowed to change what Stillwatch decides, because
what it decides is whether to wake somebody in the night.

The guarantee is not a queue. Every event carries the time the camera saw it,
every event has an id derived from its own content, and a judgement is a
function of the whole stored set read in time order. Arrival order is not an
input, so it cannot be a fault. These checks are here because that is a claim,
and a claim about a safety product should be demonstrated rather than asserted.
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["STILLWATCH_TZ"] = "UTC"
sys.path.insert(0, str(ROOT.parent / "ring-event-simulator"))

from ringsim import Simulator
from ringsim.devices import DEFAULT_DEVICES, manifest
from ringsim.scenarios import SCENARIOS

from stillwatch.model import Device, DeviceRoster, normalise
from stillwatch.monitor import ALERT, AWAY, assess
from stillwatch.rhythm import learn
from stillwatch.ring import sign
from stillwatch.service import LiveSource, create_app
from stillwatch.store import EventStore

SECRET = "delivery-order-secret"
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
    events, meta = SCENARIOS[key].build(Simulator(seed=SEED), START, DAYS)
    records = [event.to_record() for event in events]
    stream = [normalise(record) for record in records]
    target = date.fromisoformat(meta["target_day"])
    baseline = learn(stream, roster(), until=at(target, 0))
    return records, stream, baseline, target


def fingerprint(reading):
    """Everything a caregiver would be told, reduced to something comparable."""
    return (
        reading.state,
        reading.headline,
        tuple(reading.reasons),
        round(reading.silence_seconds),
        reading.departure_at,
        reading.unknown_reason,
    )


def test_order_does_not_change_a_judgement():
    section("the same day, shuffled")
    _, stream, baseline, target = prepared("fall")
    people = roster()
    moment = at(target, 12)

    expected = fingerprint(assess(stream, baseline, people, moment))
    check("the ordered stream is an alert", expected[0] == ALERT, expected[0])

    backwards = fingerprint(assess(list(reversed(stream)), baseline, people, moment))
    check("delivered backwards it reads the same", backwards == expected)

    # Not one shuffle. A single arrangement that happens to agree proves very
    # little about a stream of a few thousand events.
    shuffler = random.Random(11)
    same = 0
    for _ in range(25):
        scrambled = list(stream)
        shuffler.shuffle(scrambled)
        if fingerprint(assess(scrambled, baseline, people, moment)) == expected:
            same += 1
    check("and the same after twenty five shuffles", same == 25, "%d of 25" % same)


def test_the_discriminator_survives_a_late_door_event():
    """The reviewer's scenario, run rather than argued about.

    The whole product turns on one door event. If that event arrives after the
    interior events that follow it, a system that trusted arrival order would
    call an ordinary outing a collapse.
    """
    section("the door event arrives last")
    _, stream, baseline, target = prepared("away")
    people = roster()
    moment = at(target, 12)

    ordered = assess(stream, baseline, people, moment)
    check("in order, the outing is away and not on the ladder",
          ordered.state == AWAY, ordered.state)
    check("a door event is what explains it", ordered.departure_at is not None)

    # Everything on the target day, with the transit events moved to the back
    # of the delivery while keeping the timestamps the cameras gave them.
    transit = set(people.transit_ids())
    day_start = at(target, 0)
    held_back = ([event for event in stream if not (
        event.device_id in transit and event.at >= day_start)]
        + [event for event in stream if event.device_id in transit and event.at >= day_start])
    check("the arrangement really did move the door events",
          held_back != stream)

    late = assess(held_back, baseline, people, moment)
    check("it is still away, not an alert", late.state == AWAY, late.state)
    check("and it still names the same departure",
          late.departure_at == ordered.departure_at)
    check("nothing else about the reading moved",
          fingerprint(late) == fingerprint(ordered))


def post(client, records, secret=SECRET):
    body = json.dumps({"data": list(records)}).encode()
    return client.post("/ring/events", data=body,
                       headers={"X-Signature": sign(secret, body),
                                "Content-Type": "application/json"})


def served():
    store = EventStore()
    app = create_app(LiveSource(store, person="Margarette"), store=store,
                     webhook_secret=SECRET)
    return store, app.test_client()


def test_the_webhook_puts_a_scrambled_delivery_back_in_order():
    section("through the endpoint Ring actually posts to")
    records, _, _, target = prepared("fall")
    recent = [record for record in records if record["created_at"] >= at(target, 0).isoformat()]
    check("there is a day to deliver", len(recent) > 20, str(len(recent)))

    forward, forward_client = served()
    post(forward_client, recent)

    # One event per delivery, last first, as a retry queue draining backwards
    # would present them.
    backward, backward_client = served()
    for record in reversed(recent):
        post(backward_client, [record])

    straight = [(event.event_id, event.device_id, event.kind, event.at)
                for event in forward.events()]
    scrambled = [(event.event_id, event.device_id, event.kind, event.at)
                 for event in backward.events()]
    check("both stores hold the same number", forward.count() == backward.count(),
          "%d and %d" % (forward.count(), backward.count()))
    check("and read back identically, in time order", straight == scrambled)
    check("which is sorted", straight == sorted(straight, key=lambda row: row[3]))


def test_a_replayed_delivery_cannot_shorten_a_silence():
    """Why there is no five minute replay window, and should not be.

    A captured delivery can be sent again, but it carries the time the camera
    saw it, not the time it was replayed. Sent twice it collides on its own id
    and stores once. Sent with a fresh id it still lands in the past, where it
    cannot explain away a silence happening now. A window on the timestamp
    would reject nothing that matters here, and would throw away the genuine
    late retries and the history backfill that the baseline is learned from.
    """
    section("a replayed delivery")
    store, client = served()
    first = {"event_id": "live-1", "device_id": "kitchen", "kind": "motion",
             "created_at": "2026-09-20T08:00:00+00:00"}
    post(client, [first])
    check("the original is stored", store.count() == 1, str(store.count()))

    again = post(client, [first])
    check("replaying it is accepted", again.status_code == 200)
    check("and stores nothing further",
          again.get_json()["stored"] == 0 and store.count() == 1)

    last_before = store.last_event_at(kinds=("motion", "ding"))
    forged = post(client, [dict(first, event_id="live-1-replayed")])
    check("a replay wearing a new id is stored as the new event it claims to be",
          forged.get_json()["stored"] == 1)
    check("but it lands at the time the camera saw it, so the silence is unchanged",
          store.last_event_at(kinds=("motion", "ding")) == last_before,
          str(store.last_event_at(kinds=("motion", "ding"))))


def main():
    test_order_does_not_change_a_judgement()
    test_the_discriminator_survives_a_late_door_event()
    test_the_webhook_puts_a_scrambled_delivery_back_in_order()
    test_a_replayed_delivery_cannot_shorten_a_silence()

    print("\n%d checks passed" % PASSED)
    if FAILED:
        print("%d failed:" % len(FAILED))
        for label in FAILED:
            print("  %s" % label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
