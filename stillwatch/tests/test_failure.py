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


class Recording:
    """Stands in for CloudWatch."""

    def __init__(self, breaks=False):
        self.calls = []
        self.breaks = breaks

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        if self.breaks:
            raise RuntimeError("could not reach CloudWatch")


def values(call):
    return {d["MetricName"]: d["Value"] for d in call["MetricData"]}


def test_the_heartbeat_leaves_the_deployment():
    """The judge reports that it ran, to somewhere that cannot die with it.

    `stillwatch watchdog` runs on the same scheduler, against the same
    database, inside the same deployment. If the deployment is what broke, it
    is as likely to be broken too. A metric published outward is the only part
    of this that an alarm on missing data can see.
    """
    section("the judge reporting its own pulse")
    from stillwatch.metrics import LADDER, Reporter

    client = Recording()
    reporter = Reporter("margarette", client=client)
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    ok = reporter.tick(ALERT, at=now,
                       last_event_at=now - timedelta(minutes=62),
                       last_delivery_at=now - timedelta(minutes=3),
                       stored_events=14390)

    check("it reported", ok is True and len(client.calls) == 1)
    sent = values(client.calls[0])
    check("the heartbeat itself is there", sent.get("JudgeRan") == 1.0, str(sent))
    check("the state goes as a number an alarm can read",
          sent.get("StateLevel") == float(LADDER[ALERT]), str(sent))
    check("how stale the events are", round(sent.get("MinutesSinceEvent", -1)) == 62,
          str(sent.get("MinutesSinceEvent")))
    check("and how many are stored", sent.get("StoredEvents") == 14390.0)
    check("every point is tagged with the household",
          all(d["Dimensions"][0] == {"Name": "Household", "Value": "margarette"}
              for d in client.calls[0]["MetricData"]))

    # An alarm set on an ordinary state must not fire, so the ladder has to be
    # ordered by how much attention is wanted, not alphabetically.
    check("an ordinary day sits below a worrying one",
          LADDER["NORMAL"] < LADDER["QUIET"] < LADDER["CONCERN"] < LADDER["ALERT"])
    check("and a house that cannot be read sits above an ordinary one",
          LADDER["UNKNOWN"] > LADDER["NORMAL"])


def test_reporting_cannot_take_down_the_judging():
    """Monitoring that can break the thing it monitors is worse than none."""
    section("CloudWatch is unreachable")
    from stillwatch.metrics import Reporter

    broken = Recording(breaks=True)
    reporter = Reporter("margarette", client=broken)
    check("a failed report says so rather than raising",
          reporter.tick(ALERT) is False)

    events, meta = SCENARIOS["fall"].build(Simulator(seed=SEED), START, DAYS)
    target = date.fromisoformat(meta["target_day"])
    stream = [normalise(event.to_record()) for event in events]
    store = EventStore()
    store.remember_devices(list(roster()))
    store.add_many(stream)

    inbox = Refusing()
    inbox.name = "recorder"
    watcher = LiveWatcher(store, "Margarette", [inbox], reporter=reporter)
    noon = datetime.combine(target, time(12), tzinfo=timezone.utc)
    notices = watcher.once(noon)

    check("the tick still completed", bool(notices))
    state = store.load_state("watcher") or {}
    check("the judgement was still written down", state.get("state") == ALERT,
          str(state.get("state")))
    check("and CloudWatch was still attempted", len(broken.calls) > 0)


def test_reporting_is_off_unless_it_is_asked_for():
    section("no credentials, no reporting")
    from stillwatch.metrics import reporter_from_env

    check("off by default", reporter_from_env({}) is None)
    check("off when switched off",
          reporter_from_env({"STILLWATCH_CLOUDWATCH": "0"}) is None)
    check("and a watcher with no reporter judges exactly the same",
          LiveWatcher(EventStore(), "M", []).reporter is None)


def test_the_outside_check_reads_the_health_endpoint_correctly():
    """The check that runs on Lambda, judged without a network.

    It has to tell three things apart that all look like trouble from inside:
    a healthy site, a site that answers and says its database is gone, and a
    site that does not answer at all. Only the last of those means the
    deployment is unreachable.
    """
    section("the outside health check")
    sys.path.insert(0, str(ROOT.parent / "deploy" / "lambda"))
    import health_check

    healthy = json.dumps({"ok": True, "store_ok": True, "webhook": True,
                          "feed_recent": True})
    good = health_check.assess(200, healthy, 412.0)
    check("a healthy site is reachable", good["SiteReachable"] == 1, str(good))
    check("and its store is reported well", good["SiteStoreOk"] == 1)
    check("and the latency is carried", good["SiteLatencyMs"] == 412.0)

    # The service answering 503 with store_ok false is the case our own error
    # handling produces. It is up, and it is telling us which part is down.
    unwell = json.dumps({"ok": False, "store_ok": False, "webhook": True})
    sick = health_check.assess(503, unwell, 300.0)
    check("a site that answers but is unwell is not called reachable",
          sick["SiteReachable"] == 0, str(sick))
    check("but it did answer", sick["SiteAnswered"] == 1)
    check("and the store is named as the part that is down",
          sick["SiteStoreOk"] == 0)

    # Nothing at all: DNS, TLS, routing, or the host simply gone.
    dead = health_check.assess(None, "", 10000.0)
    check("no answer at all is not reachable", dead["SiteReachable"] == 0)
    check("and is recorded as not having answered", dead["SiteAnswered"] == 0,
          str(dead))

    # A proxy error page, which is what a CDN returns when the origin is gone.
    html = health_check.assess(502, "<html>Bad Gateway</html>", 80.0)
    check("an HTML error page is not mistaken for health",
          html["SiteReachable"] == 0, str(html))

    data = health_check.metric_data(good, household="margarette")
    names = {d["MetricName"] for d in data}
    check("every metric carries the household",
          all(d["Dimensions"] == [{"Name": "Household", "Value": "margarette"}]
              for d in data))
    check("latency is sent as milliseconds, not a count",
          next(d["Unit"] for d in data if d["MetricName"] == "SiteLatencyMs")
          == "Milliseconds")
    check("and the four readings are all published",
          names == {"SiteReachable", "SiteAnswered", "SiteStoreOk",
                    "SiteLatencyMs"}, str(names))


def main():
    test_health_still_answers_when_the_records_cannot_be_read()
    test_the_outside_check_reads_the_health_endpoint_correctly()
    test_the_heartbeat_leaves_the_deployment()
    test_reporting_cannot_take_down_the_judging()
    test_reporting_is_off_unless_it_is_asked_for()
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
