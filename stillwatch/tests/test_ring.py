"""Checks on the event store, the Ring adapter, and the webhook.

Run directly, exits non zero on failure. Nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Clocks are pinned, so a suite cannot pass or fail depending on where the
# machine running it happens to be.
os.environ["STILLWATCH_TZ"] = "UTC"

from stillwatch.model import DING, Device, Event, INTERIOR, MOTION, OFFLINE, ONLINE, TRANSIT
from stillwatch.ring import (
    RingClient,
    RingError,
    classify,
    device_from_ring,
    normalise_many,
    next_link,
    normalise_ring,
    sign,
    verify_signature,
)
from stillwatch.backfill import backfill, client_from_store
from stillwatch.service import LiveSource, create_app
from stillwatch.store import EventStore, PostgresDialect, SqliteDialect, dialect_for

SECRET = "hmac-key-from-the-portal"

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


def raises(action, kind=RingError):
    try:
        action()
    except kind:
        return True
    except Exception:
        return False
    return False


def moment(hour, minute=0, day=20):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def event(event_id, device_id="kitchen", kind=MOTION, hour=9, minute=0):
    return Event(event_id, device_id, kind, moment(hour, minute))


def test_store():
    section("the event store")
    store = EventStore()
    store.add_many([event("a", hour=8), event("b", hour=9), event("c", hour=10)])
    check("events come back in order", [e.event_id for e in store.events()] == ["a", "b", "c"])
    check("it counts what it holds", store.count() == 3)
    check("it knows the latest event", store.last_event_at() == moment(10))

    check("a redelivered event is not stored twice", store.add(event("b", hour=9)) is False)
    check("the count does not move", store.count() == 3)
    check("a new event still stores", store.add(event("d", hour=11)) is True)

    window = store.events(since=moment(9), until=moment(11))
    check("since and until narrow the window", [e.event_id for e in window] == ["b", "c"],
          str([e.event_id for e in window]))

    store.remember_devices([
        Device("front_door", "Front Door", TRANSIT),
        Device("kitchen", "Kitchen", INTERIOR),
    ])
    roster = store.roster()
    check("devices are remembered", len(roster) == 2)
    check("their classes survive", roster.is_transit("front_door") and roster.is_interior("kitchen"))

    store.remember_device(Device("kitchen", "Kitchen Camera", INTERIOR))
    check("a device can be renamed", store.roster().name("kitchen") == "Kitchen Camera")
    check("renaming does not duplicate it", len(store.roster()) == 2)

    # Ring tells us nothing about what a device is, so a doorbell press is the
    # only trustworthy evidence that a camera watches a way in or out.
    store.remember_device(Device("side", "Camera 2", INTERIOR))
    check("an unhelpfully named camera starts inside",
          store.roster().is_interior("side"))

    store.add(Event("ding-1", "side", DING, moment(9, 30)))
    check("a doorbell press makes it a way out", store.roster().is_transit("side"))

    store.remember_device(Device("side", "Camera 2", INTERIOR))
    check("a later device sync does not undo that",
          store.roster().is_transit("side"))

    store.add(Event("motion-1", "kitchen", MOTION, moment(9, 31)))
    check("motion alone never promotes a camera", store.roster().is_interior("kitchen"))


class RecordingCursor:
    def __init__(self, log):
        self.log = log
        self.rowcount = 1

    def execute(self, statement, values=()):
        self.log.append(statement)

    def fetchall(self):
        return []

    def close(self):
        pass


class RecordingConnection:
    def __init__(self):
        self.statements = []
        self.commits = 0

    def cursor(self):
        return RecordingCursor(self.statements)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


class RecordingDialect:
    """Stands in for Postgres, so the SQL we would send can be inspected."""

    name = "postgres"
    placeholder = "%s"

    def __init__(self):
        self.connection = RecordingConnection()

    def connect(self, target):
        return self.connection

    def row(self, cursor, values):
        return values


def test_both_databases():
    section("the store speaks both dialects")
    check("a Heroku database URL chooses Postgres",
          isinstance(dialect_for("postgres://user:pw@host/db"), PostgresDialect))
    check("the longer spelling does too",
          isinstance(dialect_for("postgresql://user:pw@host/db"), PostgresDialect))
    check("a file path stays on SQLite", isinstance(dialect_for("events.db"), SqliteDialect))
    check("so does memory", isinstance(dialect_for(":memory:"), SqliteDialect))

    dialect = RecordingDialect()
    store = EventStore("postgres://fake/db", dialect=dialect)
    check("it reports which database it is on", store.kind == "postgres")

    store.add_many([event("a", hour=8)])
    store.remember_device(Device("kitchen", "Kitchen", INTERIOR))
    store.save_tokens("default", "access", "refresh", moment(9))
    sent = dialect.connection.statements

    schema = " ".join(sent[:4])
    for table in ("events", "devices", "tokens"):
        check("the %s table is created" % table, "CREATE TABLE IF NOT EXISTS %s" % table in schema)

    inserts = [line for line in sent if line.startswith("INSERT INTO events")]
    check("events insert with the Postgres placeholder",
          inserts and "%s" in inserts[0] and "?" not in inserts[0], inserts[0] if inserts else "")
    check("a redelivery is ignored rather than failing",
          "ON CONFLICT (event_id) DO NOTHING" in inserts[0])

    devices = [line for line in sent if line.startswith("INSERT INTO devices")]
    check("a device is upserted", "ON CONFLICT (device_id) DO UPDATE" in devices[0])
    check("with Postgres placeholders", "?" not in devices[0])

    tokens = [line for line in sent if line.startswith("INSERT INTO tokens")]
    check("tokens are upserted", "ON CONFLICT (account) DO UPDATE" in tokens[0])
    check("with Postgres placeholders", "?" not in tokens[0])
    check("every write is committed", dialect.connection.commits >= 3)

    sqlite_store = EventStore(":memory:")
    check("on SQLite the same code uses question marks",
          sqlite_store._sql("SELECT ?") == "SELECT ?")
    check("on Postgres it uses percent s", store._sql("SELECT ?") == "SELECT %s")


def test_store_survives_a_restart():
    section("the store outlives the process")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "events.db"
        first = EventStore(path)
        first.add_many([event("a", hour=8), event("b", hour=9)])
        first.remember_device(Device("kitchen", "Kitchen", INTERIOR))
        first.close()

        second = EventStore(path)
        check("events are still there", second.count() == 2)
        check("devices are still there", len(second.roster()) == 1)
        check("a redelivery after a restart is still caught",
              second.add(event("a", hour=8)) is False)
        second.close()


def test_ring_words():
    section("Ring's words become ours")
    pairs = [("motion_detected", MOTION), ("button_press", DING),
             ("device_online", ONLINE), ("device_offline", OFFLINE)]
    for ring_kind, ours in pairs:
        parsed = normalise_ring({
            "event_id": "e1", "device_id": "d1", "event_type": ring_kind,
            "created_at": "2026-09-20T09:14:00Z",
        })
        check("%s becomes %s" % (ring_kind, ours), parsed.kind == ours, parsed.kind)

    check("a Z timestamp is read as UTC",
          normalise_ring({"event_id": "e", "device_id": "d", "event_type": "motion_detected",
                          "created_at": "2026-09-20T09:14:00Z"}).at == moment(9, 14))

    for ignored in ("subscription_activated", "device_added", "app_integration_removed"):
        check("%s is ignored" % ignored,
              normalise_ring({"event_id": "e", "device_id": "d", "event_type": ignored,
                              "created_at": "2026-09-20T09:14:00Z"}) is None)
    check("an unknown event type is ignored rather than guessed",
          normalise_ring({"event_id": "e", "device_id": "d", "event_type": "sky_fell",
                          "created_at": "2026-09-20T09:14:00Z"}) is None)


def test_ring_shapes():
    section("the shapes Ring sends")
    wrapped = normalise_ring({
        "data": {
            "id": "evt-9",
            "type": "event",
            "attributes": {"event_type": "motion_detected", "created_at": "2026-09-20T09:14:00Z"},
            "relationships": {"device": {"data": {"id": "kitchen-1", "type": "device"}}},
        }
    })
    check("a JSON:API envelope is unwrapped", wrapped.event_id == "evt-9", str(wrapped))
    check("the device comes out of the relationships", wrapped.device_id == "kitchen-1")

    many = normalise_many({"data": [
        {"id": "1", "device_id": "d", "event_type": "motion_detected", "created_at": "2026-09-20T09:00:00Z"},
        {"id": "2", "device_id": "d", "event_type": "subscription_activated", "created_at": "2026-09-20T09:01:00Z"},
        {"id": "3", "device_id": "d", "event_type": "button_press", "created_at": "2026-09-20T09:02:00Z"},
    ]})
    check("a page of events keeps only the useful ones",
          [e.event_id for e in many] == ["1", "3"], str([e.event_id for e in many]))
    check("a single event posts as well as a list",
          len(normalise_many({"event_id": "x", "device_id": "d",
                              "event_type": "motion_detected",
                              "created_at": "2026-09-20T09:00:00Z"})) == 1)

    body = {"device_id": "d", "event_type": "motion_detected", "created_at": "2026-09-20T09:14:00Z"}
    first = normalise_ring(dict(body))
    second = normalise_ring(dict(body))
    check("an event with no id still gets one", first.event_id.startswith("ring_"))
    check("and the same event always gets the same one", first.event_id == second.event_id)
    other = dict(body, created_at="2026-09-20T09:15:00Z")
    check("a different event gets a different one",
          normalise_ring(other).event_id != first.event_id)

    check("an event with no type is refused",
          raises(lambda: normalise_ring({"device_id": "d", "created_at": "2026-09-20T09:00:00Z"})))
    check("an event naming no device is refused",
          raises(lambda: normalise_ring({"event_type": "motion_detected",
                                         "created_at": "2026-09-20T09:00:00Z"})))
    check("an event with no time is refused",
          raises(lambda: normalise_ring({"event_type": "motion_detected", "device_id": "d"})))


def test_classification():
    section("which cameras watch a way out")
    for name in ("Front Door", "Back Porch", "Side Gate", "Driveway", "Garage"):
        check("%s is a way out" % name, classify(name) == TRANSIT)
    for name in ("Kitchen", "Living Room", "Landing", "Hallway", "Bedroom"):
        check("%s is inside" % name, classify(name) == INTERIOR)
    check("an unnamed camera is treated as inside, which is the safer mistake",
          classify("") == INTERIOR)

    device = device_from_ring({"data": {"id": "dev-1", "attributes": {"name": "Front Door"}}})
    check("a Ring device becomes one of ours", device.device_id == "dev-1"
          and device.name == "Front Door" and device.zone_class == TRANSIT)


def test_signatures():
    section("webhook signatures")
    body = b'{"event_type":"motion_detected"}'
    header = sign(SECRET, body)
    check("a signature is hex with the documented prefix",
          header.startswith("sha256=") and len(header) == 71, header)
    check("our own signature verifies", verify_signature(SECRET, body, header))
    check("whitespace around it is tolerated", verify_signature(SECRET, body, " " + header + "\n"))
    check("a different key fails", not verify_signature("other-key", body, header))
    check("a tampered body fails", not verify_signature(SECRET, body + b" ", header))
    check("a missing header fails", not verify_signature(SECRET, body, ""))
    check("no secret configured fails", not verify_signature("", body, header))
    check("the prefix is required", not verify_signature(SECRET, body, header[7:]))


class FakeRing:
    """Stands in for Ring's HTTP endpoints."""

    def __init__(self):
        self.calls = []
        self.expires_in = 14400

    def __call__(self, method, url, payload):
        self.calls.append((method, url, payload))
        if method == "POST":
            grant = payload.get("grant_type")
            return {
                "access_token": "token-for-" + grant,
                "refresh_token": "refresh-2",
                "expires_in": self.expires_in,
                "token_type": "Bearer",
                "scope": "ava.v1:read",
            }
        return {"data": [
            {"id": "h1", "device_id": "kitchen", "event_type": "motion_detected",
             "created_at": "2026-09-20T09:00:00Z"},
            {"id": "h2", "device_id": "front_door", "event_type": "button_press",
             "created_at": "2026-09-20T09:05:00Z"},
        ]}


def test_client():
    section("talking to Ring")
    fake = FakeRing()
    client = RingClient("client-id", "secret", transport=fake)

    token = client.exchange("auth-code")
    check("an authorisation code is exchanged for a token", token == "token-for-authorization_code")
    check("it posts to the documented token endpoint",
          fake.calls[0][1] == "https://oauth.ring.com/oauth/token", fake.calls[0][1])
    check("it sends the client credentials with the code",
          fake.calls[0][2]["client_id"] == "client-id" and fake.calls[0][2]["code"] == "auth-code")
    check("the refresh token is kept", client.refresh_token == "refresh-2")

    before = len(fake.calls)
    check("a valid token is reused rather than refetched",
          client.token() == token and len(fake.calls) == before)

    client.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    check("an expired token is renewed", client.token() == "token-for-refresh_token")
    check("renewal uses the refresh grant", fake.calls[-1][2]["grant_type"] == "refresh_token")

    events = client.history("kitchen", since=moment(0))
    check("history comes back as our events", [e.kind for e in events] == [MOTION, DING],
          str([e.kind for e in events]))
    check("history asks the documented endpoint",
          fake.calls[-1][1].startswith("https://api.amazonvision.com/v1/history/devices/kitchen/events"),
          fake.calls[-1][1])
    check("the since filter is passed on", "since=" in fake.calls[-1][1])

    check("a token is renewed a minute before it expires",
          client.expires_at < datetime.now(timezone.utc) + timedelta(seconds=fake.expires_in))
    check("renewing without a refresh token is refused",
          raises(lambda: RingClient("a", "b", transport=fake).refresh()))


def serving():
    store = EventStore()
    app = create_app(LiveSource(store, person="Margarette"), store=store, webhook_secret=SECRET)
    return store, app.test_client()


def post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post("/ring/events", data=body,
                       headers={"X-Signature": sign(secret, body),
                                "Content-Type": "application/json"})


def test_webhook():
    section("the endpoint Ring posts to")
    store, client = serving()

    payload = {"data": [
        {"id": "w1", "device_id": "kitchen", "event_type": "motion_detected",
         "created_at": "2026-09-20T09:00:00Z"},
        {"id": "w2", "device_id": "front_door", "event_type": "button_press",
         "created_at": "2026-09-20T09:01:00Z"},
    ]}
    response = post(client, payload)
    check("a signed delivery is accepted", response.status_code == 200, str(response.status_code))
    check("both events are stored", store.count() == 2, str(store.count()))
    check("it says what it stored", response.get_json()["stored"] == 2)

    again = post(client, payload)
    check("a redelivery is accepted", again.status_code == 200)
    check("but nothing is stored twice", again.get_json()["stored"] == 0 and store.count() == 2)

    forged = post(client, {"data": [{"id": "bad", "device_id": "kitchen",
                                     "event_type": "motion_detected",
                                     "created_at": "2026-09-20T10:00:00Z"}]},
                  secret="wrong-key")
    check("an unsigned or wrongly signed delivery is refused", forged.status_code == 401)
    check("nothing from it is stored", store.count() == 2)

    body = b"{not json"
    broken = client.post("/ring/events", data=body,
                         headers={"X-Signature": sign(SECRET, body)})
    check("an unreadable payload is not retried forever", broken.status_code == 200,
          str(broken.status_code))
    check("and nothing is stored from it", store.count() == 2)

    health = client.get("/api/health").get_json()
    check("health reports the webhook is armed", health["webhook"] is True)
    check("health counts stored events", health["stored_events"] == 2)


def test_webhook_without_a_secret():
    section("a webhook with nothing configured")
    store = EventStore()
    client = create_app(LiveSource(store), store=store, webhook_secret="").test_client()
    body = json.dumps({"event_type": "motion_detected"}).encode()
    response = client.post("/ring/events", data=body, headers={"X-Signature": "sha256=x"})
    check("it refuses traffic rather than accepting unchecked events",
          response.status_code == 503, str(response.status_code))
    check("health says the webhook is not armed",
          client.get("/api/health").get_json()["webhook"] is False)


def test_live_day():
    section("the dashboard reading real events")
    store, client = serving()
    today = datetime.now(timezone.utc).date()

    store.remember_devices([
        Device("kitchen", "Kitchen", INTERIOR),
        Device("front_door", "Front Door", TRANSIT),
    ])
    base = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    store.add_many([
        Event("l%d" % index, "kitchen", MOTION, base + timedelta(hours=7, minutes=index * 3))
        for index in range(8)
    ])

    listing = client.get("/api/scenarios").get_json()
    check("live is offered as the day to watch",
          [entry["key"] for entry in listing["scenarios"]] == ["live"])

    day = client.get("/api/day?scenario=live").get_json()
    check("it is marked live", day["live"] is True)
    check("it is today", day["day"] == today.isoformat(), day["day"])
    check("it stops at the present rather than midnight",
          len(day["readings"]) < 288 and len(day["readings"]) > 0, str(len(day["readings"])))
    check("it carries the stored devices", len(day["devices"]) == 2)
    check("it says when it was taken", bool(day["as_of"]))

    first = len(day["readings"])
    store.add(Event("later", "kitchen", MOTION, base + timedelta(hours=7, minutes=30)))
    second = client.get("/api/day?scenario=live").get_json()
    check("a live day is never served from a cache",
          second["today"]["kitchen"] != day["today"]["kitchen"] or len(second["readings"]) >= first)


class PagedRing:
    """Ring with several pages of history and one camera that errors."""

    def __init__(self, pages=2, broken=None, next_host="api.amazonvision.com"):
        self.calls = []
        self.pages = pages
        self.broken = broken or set()
        self.next_host = next_host

    def __call__(self, method, url, payload):
        self.calls.append((method, url, payload))
        if method == "POST":
            return {"access_token": "t", "refresh_token": "r", "expires_in": 14400}
        if "/devices?" in url or url.endswith("/devices"):
            return {"data": [
                {"id": "front_door", "attributes": {"name": "Front Door"}},
                {"id": "kitchen", "attributes": {"name": "Kitchen"}},
                {"id": "landing", "attributes": {"name": "Landing"}},
            ]}

        device = url.split("/devices/")[1].split("/")[0]
        if device in self.broken:
            raise RingError("500 from Ring")

        page = 2 if "page=2" in url else 1
        events = [{
            "id": "%s-p%d-%d" % (device, page, index),
            "device_id": device,
            "event_type": "motion_detected",
            "created_at": "2026-09-%02dT%02d:00:00Z" % (10 + page, 8 + index),
        } for index in range(2)]

        body = {"data": events}
        if page < self.pages:
            body["links"] = {"next": "https://%s/v1/history/devices/%s/events?page=2"
                             % (self.next_host, device)}
        return body


def test_backfill():
    section("reading the past out of Ring")
    store = EventStore()
    fake = PagedRing()
    client = RingClient("id", "secret", refresh_token="r", transport=fake)

    report = backfill(client, store, days=30)
    check("every device is found", report.devices == 3, str(report.devices))
    check("their names and classes are stored",
          store.roster().is_transit("front_door") and store.roster().is_interior("kitchen"))
    check("both pages are followed", report.per_device["kitchen"] == 4,
          str(report.per_device))
    check("everything read was new", report.stored == report.fetched == 12,
          "%d fetched, %d stored" % (report.fetched, report.stored))
    check("the store holds it", store.count() == 12, str(store.count()))
    check("the span of history is reported", report.span_days() > 0, str(report.span_days()))
    check("it reports success", report.ok)

    again = backfill(client, store, days=30)
    check("running it twice reads the same events", again.fetched == 12)
    check("but stores none of them again", again.stored == 0, str(again.stored))
    check("the store has not grown", store.count() == 12)

    check("the history call asks the documented endpoint",
          any("/v1/history/devices/kitchen/events" in call[1] for call in fake.calls))
    check("the device list asks the documented endpoint",
          any(call[1].startswith("https://api.amazonvision.com/v1/devices") for call in fake.calls))
    check("a since filter is sent", any("since=" in call[1] for call in fake.calls))
    check("the refreshed token is kept for next time", store.is_linked())


def test_backfill_survives_a_broken_camera():
    section("one camera fails, the rest still load")
    store = EventStore()
    client = RingClient("id", "secret", refresh_token="r",
                        transport=PagedRing(broken={"kitchen"}))

    report = backfill(client, store, days=30)
    check("the failure is reported", not report.ok and len(report.failures) == 1,
          str(report.failures))
    check("it names the camera that failed", report.failures[0][0] == "kitchen")
    check("the other two still loaded", report.fetched == 8, str(report.fetched))
    check("and their events are stored", store.count() == 8)
    check("the broken one contributed nothing", "kitchen" not in report.per_device)
    check("the summary mentions the failure", "kitchen" in report.summary())


def test_backfill_will_not_follow_a_link_elsewhere():
    """A next link is data from the network, not an instruction."""
    section("pagination links are not followed off Ring")
    check("a link to another host is refused",
          next_link({"links": {"next": "https://evil.example/v1/history"}}) is None)
    check("a link on Ring is followed",
          next_link({"links": {"next": "https://api.amazonvision.com/v1/x"}})
          == "https://api.amazonvision.com/v1/x")
    check("plain http is refused",
          next_link({"links": {"next": "http://api.amazonvision.com/v1/x"}}) is None)
    check("a reply with no links ends the paging", next_link({"data": []}) is None)

    store = EventStore()
    client = RingClient("id", "secret", refresh_token="r",
                        transport=PagedRing(next_host="evil.example"))
    report = backfill(client, store, days=30)
    check("so a hostile next link stops the paging instead of being fetched",
          report.fetched == 6, str(report.fetched))


def test_backfill_needs_a_linked_home():
    section("backfill before linking")
    store = EventStore()
    env = {"STILLWATCH_RING_CLIENT_ID": "id", "STILLWATCH_RING_CLIENT_SECRET": "secret"}
    check("it refuses when no home is linked",
          raises(lambda: client_from_store(store, env)))

    store.save_tokens("default", "access", "refresh", moment(9))
    client = client_from_store(store, env)
    check("once linked it carries the stored refresh token", client.refresh_token == "refresh")
    check("and the access token", client.access_token == "access")
    check("it refuses without app credentials",
          raises(lambda: client_from_store(store, {})))


def test_account_linking():
    section("account linking")
    store = EventStore()
    fake = FakeRing()
    client = RingClient("client-id", "secret", transport=fake)
    app = create_app(LiveSource(store, "Margarette"), store=store,
                     webhook_secret=SECRET, ring=client)
    web = app.test_client()

    page = web.get("/ring/link?redirect_uri=https://ring.example/done")
    check("the link page is served", page.status_code == 200)
    body = page.data.decode()
    check("it sends people back where Ring asked", "https://ring.example/done" in body)
    flowed = " ".join(body.split())
    check("it says what is watched and what is not",
          "never watches video" in flowed and "never stores a picture" in flowed, flowed[:160])

    nasty = web.get('/ring/link?redirect_uri="><script>alert(1)</script>')
    check("a hostile return address cannot inject markup",
          b"<script>alert" not in nasty.data, nasty.data.decode()[:200])

    check("nothing is linked yet", store.is_linked() is False)
    check("health says so", web.get("/api/health").get_json()["ring_linked"] is False)

    refused = web.post("/ring/token", json={})
    check("a request with no code is refused", refused.status_code == 400, str(refused.status_code))

    done = web.post("/ring/token", json={"code": "auth-code-from-ring"})
    check("a code is accepted", done.status_code == 200, str(done.status_code))
    check("it was swapped at Ring's token endpoint",
          fake.calls[-1][1] == "https://oauth.ring.com/oauth/token")
    check("the grant type is the authorisation code",
          fake.calls[-1][2]["grant_type"] == "authorization_code")

    stored = store.tokens()
    check("the tokens are kept in the store", stored["refresh"] == "refresh-2")
    check("with an expiry", stored["expires_at"] is not None)
    check("health now reports the home as linked",
          web.get("/api/health").get_json()["ring_linked"] is True)

    check("tokens are never served over the API",
          b"refresh-2" not in web.get("/api/health").data)


def test_token_endpoint_without_credentials():
    section("linking before the app is configured")
    store = EventStore()
    web = create_app(LiveSource(store), store=store, webhook_secret=SECRET).test_client()
    response = web.post("/ring/token", json={"code": "x"})
    check("it refuses rather than pretending to link", response.status_code == 503,
          str(response.status_code))
    check("and nothing is stored", store.is_linked() is False)


def main():
    for test in (
        test_store,
        test_both_databases,
        test_store_survives_a_restart,
        test_ring_words,
        test_ring_shapes,
        test_classification,
        test_signatures,
        test_client,
        test_webhook,
        test_webhook_without_a_secret,
        test_live_day,
        test_backfill,
        test_backfill_survives_a_broken_camera,
        test_backfill_will_not_follow_a_link_elsewhere,
        test_backfill_needs_a_linked_home,
        test_account_linking,
        test_token_endpoint_without_credentials,
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
