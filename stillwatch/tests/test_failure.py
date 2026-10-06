"""Checks on what happens when Stillwatch itself is what has broken.

A dead service looks exactly like a quiet house, and a page that fails to load
looks exactly like a page with nothing to report. For a product that watches
somebody living alone, those are the two mistakes that matter most, because
both of them read as reassurance.

So the rule these checks exist to hold: a fault here must be visible as a fault
here, must never be phrased as a reading of the home, and must not be able to
take down the one endpoint that says which part is broken.
"""

from __future__ import annotations

import json
import os
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

from dataclasses import replace

from stillwatch.model import Device, DeviceRoster, Event, normalise
from stillwatch.monitor import ALERT, NO_CONTACT, UNKNOWN, assess
from stillwatch.notify import deliver
from stillwatch.ring import sign
from stillwatch.rhythm import learn
from stillwatch.service import LiveSource, create_app
from stillwatch.store import EventStore
from stillwatch.watch import LiveWatcher

SEED = 7
START = date(2026, 8, 24)
DAYS = 28

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


class Unreachable:
    """A store whose database has gone away between one request and the next.

    Not a store that was never built. Managed Postgres closes idle connections,
    and the interesting case is the one where everything was fine a moment ago.
    """

    def __init__(self):
        self.asked = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def gone(*args, **kwargs):
            self.asked.append(name)
            raise RuntimeError("connection to the database was lost")

        return gone


def test_health_still_answers_when_the_records_cannot_be_read():
    section("the database is unreachable")
    broken = Unreachable()
    client = create_app(LiveSource(broken, person="Margarette"), store=broken,
                        webhook_secret="x").test_client()

    reply = client.get("/api/health")
    check("health answers rather than crashing", reply.status_code == 503,
          str(reply.status_code))

    body = reply.get_json()
    check("it answers in JSON, so something can act on it", body is not None)
    check("it says it is not ok", body and body.get("ok") is False, json.dumps(body))
    check("and names the store as the part that is down",
          body and body.get("store_ok") is False)
    check("it still reports whether the webhook is armed",
          body and body.get("webhook") is True)
    check("it did try to reach the store", "is_linked" in broken.asked,
          str(broken.asked))


def test_a_fault_of_our_own_is_not_reported_as_a_reading():
    section("a request that fails inside Stillwatch")
    broken = Unreachable()
    client = create_app(LiveSource(broken, person="Margarette"), store=broken,
                        webhook_secret="x").test_client()

    reply = client.get("/api/day?scenario=live")
    check("the day is refused with a server error", reply.status_code == 500,
          str(reply.status_code))

    body = reply.get_json()
    check("the reason comes back as JSON the page can read", body is not None,
          str(reply.data[:120]))
    message = (body or {}).get("error", "")
    check("it says the fault is Stillwatch's own", "fault of its own" in message,
          message)
    check("and says plainly that it is not a reading of the home",
          "not a reading of the home" in message, message)
    check("it does not leak what broke internally",
          "database" not in message.lower() and "traceback" not in message.lower(),
          message)


def test_a_refusal_worth_reading_reaches_the_page_as_a_sentence():
    """Every API refusal has to arrive as something the script can read.

    The dashboard shows the server's own wording when there is any. An HTML
    error page gives it nothing, so it falls back to saying it could not
    reach Stillwatch, which is true but tells the reader nothing they could
    act on when the real answer was "sign in" or "that day is before this
    home had any history".
    """
    section("a refusal the page should repeat")

    store = EventStore()
    locked = create_app(
        LiveSource(store, person="Margarette"), store=store, webhook_secret="k",
        environ={"STILLWATCH_PASSCODE": "NOT-A-REAL-CODE",
                 "STILLWATCH_SESSION_SECRET": "x" * 32}).test_client()

    signed_out = locked.get("/api/day?scenario=live")
    check("a signed out request is refused", signed_out.status_code == 401,
          str(signed_out.status_code))
    body = signed_out.get_json()
    check("and says so in JSON rather than an HTML page", body is not None,
          str(signed_out.data[:80]))
    check("with wording the page can show", "sign in" in (body or {}).get("error", ""),
          str(body))

    open_store = EventStore()
    nostore = create_app(LiveSource(open_store), store=None,
                         webhook_secret="").test_client()
    answered = nostore.post("/api/answer", json={"episode": "x", "outcome": "fine"})
    check("an endpoint with nothing configured refuses with its reason",
          answered.status_code == 503, str(answered.status_code))
    check("also as JSON", answered.get_json() is not None,
          str(answered.data[:80]))

    check("an unknown API path is still JSON",
          nostore.get("/api/nope").get_json() == {"error": "not found"})

    # The page itself must keep its HTML, or a mistyped address downloads a
    # file instead of showing a page.
    page = nostore.get("/nope")
    check("a mistyped page address still gets a page",
          page.status_code == 404 and "html" in page.headers.get("Content-Type", ""),
          page.headers.get("Content-Type"))


def roster():
    return DeviceRoster(
        Device(entry["device_id"], entry["name"], entry["zone_class"])
        for entry in manifest(DEFAULT_DEVICES)
    )


class Refusing:
    """A channel that is configured, reachable and failing. The SNS outage."""

    name = "sns"
    reaches_people = True

    def __init__(self):
        self.tried = 0

    def send(self, notice):
        self.tried += 1
        raise RuntimeError("Service Unavailable")


def test_a_channel_outage_does_not_stop_the_judging():
    section("notification delivery is failing")
    events, meta = SCENARIOS["fall"].build(Simulator(seed=SEED), START, DAYS)
    target = date.fromisoformat(meta["target_day"])
    stream = [normalise(event.to_record()) for event in events]

    store = EventStore()
    store.remember_devices(list(roster()))
    store.add_many(stream)

    broken = Refusing()
    watcher = LiveWatcher(store, "Margarette", [broken])
    noon = datetime.combine(target, time(12), tzinfo=timezone.utc)
    notices = watcher.once(noon)

    check("it tried to send something", broken.tried > 0, str(broken.tried))
    check("the failure is recorded against the notice",
          notices and notices[0].errors, str(notices and notices[0].errors))
    check("and the notice does not claim to have reached anybody",
          notices and not notices[0].reached_people)

    # The part that matters. A caregiver who opens the dashboard after a failed
    # send must still see the alarm that could not be delivered.
    state = store.load_state("watcher") or {}
    check("the watcher still recorded that it ran", bool(state.get("at")),
          json.dumps(state))
    check("and still recorded an alert", state.get("state") == ALERT,
          str(state.get("state")))

    client = create_app(LiveSource(store, person="Margarette"), store=store,
                        webhook_secret="x").test_client()
    health = client.get("/api/health").get_json()
    check("health reports the watcher as alive", health.get("watching") is True,
          json.dumps(health))


def test_every_channel_failing_is_still_not_silent():
    section("nothing can be delivered at all")
    first, second = Refusing(), Refusing()
    second.name = "email"

    class Notice:
        def __init__(self):
            self.errors = []
            self.delivered_to = []
            self.reached_people = False

    notice = deliver([first, second], Notice())
    check("every channel was tried, not just the first",
          first.tried == 1 and second.tried == 1,
          "%d and %d" % (first.tried, second.tried))
    check("both failures are named", len(notice.errors) == 2, str(notice.errors))
    check("each error says which channel it came from",
          any(error.startswith("sns:") for error in notice.errors)
          and any(error.startswith("email:") for error in notice.errors),
          str(notice.errors))
    check("and nothing is marked as having reached a person",
          not notice.reached_people and not notice.delivered_to)


def test_a_feed_that_died_weeks_ago_is_not_an_emergency():
    """The difference between nothing moving and nothing arriving.

    A store that was filled once and never fed again has no heartbeat and no
    delivery recorded, so there is no contact signal to go stale. Judged on
    the events alone it reports an alarm with a silence measured in weeks,
    which is not a reading of anybody. It is an instrument that has stopped.
    """
    section("a feed that stopped weeks ago")
    events, meta = SCENARIOS["fall"].build(Simulator(seed=SEED), START, DAYS)
    stream = [normalise(event.to_record()) for event in events]
    people = roster()
    now = datetime.now(timezone.utc)

    def landed(hours_ago):
        shift = (now - timedelta(hours=hours_ago)) - stream[-1].at
        moved = [replace(event, at=event.at + shift) for event in stream]
        baseline = learn(moved, people, until=moved[-1].at)
        return assess(moved, baseline, people, now)

    recent = landed(17)
    check("a collapse seventeen hours old is still an alarm",
          recent.state == ALERT, recent.state)
    check("and still says how long it has been",
          "17h" in recent.headline, recent.headline[:80])

    two_days = landed(48)
    check("two days of silence is still an alarm, not a shrug",
          two_days.state == ALERT, two_days.state)

    stale = landed(24 * 42)
    check("six weeks of nothing is not an alarm", stale.state != ALERT, stale.state)
    check("it is unknown, for want of contact", stale.state == UNKNOWN
          and stale.unknown_reason == NO_CONTACT,
          "%s / %s" % (stale.state, stale.unknown_reason))
    check("and it says nothing arrived rather than nobody moved",
          "arrived" in stale.headline, stale.headline[:90])


def test_one_unstorable_event_does_not_cost_the_batch():
    """Ring delivers several events at once. One bad one must not sink them.

    Postgres abandons the rest of a transaction after a failed statement, so
    without a savepoint around each row a single event the database will not
    accept would throw away every good event delivered beside it, and a
    webhook carrying a morning of movement would land nothing at all.
    """
    section("a batch with one event the database refuses")
    store = EventStore()
    base = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)

    good_first = Event("g1", "kitchen", "motion", base)
    # device_id is NOT NULL, so the database itself rejects this row.
    unstorable = Event("b1", None, "motion", base + timedelta(minutes=1))
    good_last = Event("g2", "landing", "motion", base + timedelta(minutes=2))

    stored = store.add_many([good_first, unstorable, good_last])
    check("it reports only what it actually stored", stored == 2, str(stored))
    check("the good event before the bad one survived",
          any(event.event_id == "g1" for event in store.events()))
    check("and so did the one after it",
          any(event.event_id == "g2" for event in store.events()))
    check("the refused event is not in the store",
          not any(event.event_id == "b1" for event in store.events()))
    check("and the store is still usable afterwards",
          store.add_many([Event("g3", "hallway", "motion",
                                base + timedelta(minutes=3))]) == 1)
    check("so the count is every good event, and nothing else",
          store.count() == 3, str(store.count()))


def test_the_webhook_keeps_what_it_can_from_a_mixed_delivery():
    section("a delivery where one event is unreadable")
    secret = "mixed-delivery-secret"
    store = EventStore()
    client = create_app(LiveSource(store, person="Margarette"), store=store,
                        webhook_secret=secret).test_client()
    body = json.dumps({"data": [
        {"id": "w1", "device_id": "kitchen", "event_type": "motion_detected",
         "created_at": "2026-09-20T09:00:00Z"},
        {"id": "w2", "device_id": "kitchen", "event_type": "motion_detected"},
        {"id": "w3", "device_id": "landing", "event_type": "motion_detected",
         "created_at": "2026-09-20T09:02:00Z"},
    ]}).encode()
    reply = client.post("/ring/events", data=body,
                        headers={"X-Signature": sign(secret, body),
                                 "Content-Type": "application/json"})

    # The middle event has no timestamp, so it cannot be placed in time and
    # is skipped. The two that were legible are kept, because losing a
    # morning of movement over one malformed record would be the worse answer.
    check("the delivery is answered, not left to be retried",
          reply.status_code == 200, str(reply.status_code))
    check("the two readable events are kept",
          reply.get_json().get("stored") == 2, str(reply.get_json()))
    check("and the unreadable one is not in the store",
          store.count() == 2, str(store.count()))

    # The same delivery without the unreadable event lands in full.
    body = json.dumps({"data": [
        {"id": "w1", "device_id": "kitchen", "event_type": "motion_detected",
         "created_at": "2026-09-20T09:00:00Z"},
        {"id": "w3", "device_id": "landing", "event_type": "motion_detected",
         "created_at": "2026-09-20T09:02:00Z"},
    ]}).encode()
    again = client.post("/ring/events", data=body,
                        headers={"X-Signature": sign(secret, body),
                                 "Content-Type": "application/json"})
    check("redelivering the same two stores neither twice",
          again.get_json().get("stored") == 0 and store.count() == 2,
          str(again.get_json()))


def main():
    test_health_still_answers_when_the_records_cannot_be_read()
    test_one_unstorable_event_does_not_cost_the_batch()
    test_the_webhook_keeps_what_it_can_from_a_mixed_delivery()
    test_a_feed_that_died_weeks_ago_is_not_an_emergency()
    test_a_fault_of_our_own_is_not_reported_as_a_reading()
    test_a_refusal_worth_reading_reaches_the_page_as_a_sentence()
    test_a_channel_outage_does_not_stop_the_judging()
    test_every_channel_failing_is_still_not_silent()

    print("\n%d checks passed" % PASSED)
    if FAILED:
        print("%d failed:" % len(FAILED))
        for label in FAILED:
            print("  %s" % label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
