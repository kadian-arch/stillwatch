"""Checks on the dashboard service. Run directly, exits non zero on failure.

Needs the replay data: from the project folder run python demo_data.py first.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Clocks are pinned, so a suite cannot pass or fail depending on where the
# machine running it happens to be.
os.environ["STILLWATCH_TZ"] = "UTC"

from stillwatch.model import Device, Event, MOTION
from stillwatch.service import CompositeSource, LiveSource, ReplaySource, create_app
from stillwatch.store import EventStore

DATA = ROOT / "data"

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


def live_app(environ, channels=None):
    """A live household with a day of history, behind whatever gate is asked for."""
    from datetime import datetime, timedelta, timezone

    store = EventStore(":memory:")
    store.remember_devices([
        Device("kitchen", "Kitchen", "interior"),
        Device("front_door", "Front Door", "transit"),
    ])
    start = datetime.now(timezone.utc) - timedelta(days=3)
    store.add_many([
        Event("seed-%d" % n, "kitchen", MOTION, start + timedelta(hours=n))
        for n in range(60)
    ])
    source = CompositeSource(LiveSource(store, person="Margarette"), ReplaySource(DATA))
    app = create_app(source, store=store, webhook_secret="hmac", environ=environ,
                     channels=channels)
    return app.test_client(), store


class Recorder:
    """Stands in for a caregiver's inbox."""

    name = "recorder"

    def __init__(self):
        self.notices = []

    def send(self, notice):
        self.notices.append(notice)


def test_the_gate():
    section("the household behind a passcode")
    locked = {"STILLWATCH_PASSCODE": "open-sesame-2026",
              "STILLWATCH_SESSION_SECRET": "a-signing-key"}
    client, _ = live_app(locked)

    check("the page itself still loads, so there is somewhere to sign in",
          client.get("/").status_code == 200)
    check("health says it is locked", client.get("/api/health").get_json()["locked"] is True)
    check("and says nobody is signed in", client.get("/api/session").get_json()["name"] is None)

    check("the household is refused",
          client.get("/api/day?scenario=live").status_code == 401)
    check("so is a single assessment of it",
          client.get("/api/assess?scenario=live").status_code == 401)
    check("but a recorded day is open to anyone",
          client.get("/api/day?scenario=fall").status_code == 200)

    check("answering is refused",
          client.post("/api/answer", json={"episode": "2026-09-20T09:00:00+00:00",
                                           "outcome": "fine"}).status_code == 401)

    check("a wrong passcode is refused",
          client.post("/api/session", json={"name": "Stranger",
                                            "passcode": "guess"}).status_code == 401)
    check("a passcode with no name is refused",
          client.post("/api/session", json={"passcode": "open-sesame-2026"}).status_code == 400)

    good = client.post("/api/session", json={"name": "Aline", "passcode": "open-sesame-2026"})
    check("the right passcode signs you in", good.status_code == 200, str(good.status_code))
    check("and the service knows who you are", good.get_json()["name"] == "Aline")
    check("the cookie cannot be read by a script",
          "HttpOnly" in good.headers.get("Set-Cookie", ""), good.headers.get("Set-Cookie", ""))

    check("now the household is visible",
          client.get("/api/day?scenario=live").status_code == 200)

    client.delete("/api/session")
    check("signing out closes it again",
          client.get("/api/day?scenario=live").status_code == 401)


def test_the_gate_cannot_be_sat_on():
    section("guessing the passcode")
    client, _ = live_app({"STILLWATCH_PASSCODE": "open-sesame-2026",
                          "STILLWATCH_SESSION_SECRET": "a-signing-key"})
    codes = [client.post("/api/session", json={"name": "Bot", "passcode": str(n)}).status_code
             for n in range(14)]
    check("the first few guesses are refused", codes[0] == 401, str(codes[:3]))
    check("and then it stops answering at all", 429 in codes, str(codes))
    check("a correct passcode cannot be slipped in behind the limit",
          client.post("/api/session",
                      json={"name": "Bot", "passcode": "open-sesame-2026"}).status_code == 429)


def test_a_forged_cookie_is_not_a_session():
    section("a cookie somebody made up")
    client, _ = live_app({"STILLWATCH_PASSCODE": "open-sesame-2026",
                          "STILLWATCH_SESSION_SECRET": "a-signing-key"})
    from stillwatch import access

    forged = access.issue("Intruder", b"a-different-key")
    client.set_cookie(access.COOKIE, forged)
    check("a cookie signed with the wrong key is ignored",
          client.get("/api/day?scenario=live").status_code == 401)

    client.set_cookie(access.COOKIE, "eyJuYW1lIjoiSW50cnVkZXIifQ.00")
    check("so is one with no real signature at all",
          client.get("/api/day?scenario=live").status_code == 401)


def test_an_answer_carries_a_name_and_tells_everyone():
    section("somebody answers, and the rest of the family hear")
    inbox = Recorder()
    client, store = live_app({"STILLWATCH_PASSCODE": "open-sesame-2026",
                              "STILLWATCH_SESSION_SECRET": "a-signing-key"},
                             channels=[inbox])
    client.post("/api/session", json={"name": "Aline", "passcode": "open-sesame-2026"})

    episode = "2026-09-20T09:00:00+00:00"
    reply = client.post("/api/answer", json={"episode": episode, "outcome": "fine"})
    check("the answer is accepted", reply.status_code == 200, str(reply.status_code))
    check("it records who gave it", reply.get_json()["by"] == "Aline")
    check("the store keeps the name", store.answer_for(episode)["by"] == "Aline")

    check("everybody else is told", len(inbox.notices) == 1, str(len(inbox.notices)))
    told = inbox.notices[0]
    check("and told who went", "Aline" in told.body, told.body)
    check("the message is not another alarm", told.urgency == "info", told.urgency)


def test_an_open_service_says_so():
    section("no passcode set")
    client, _ = live_app({})
    health = client.get("/api/health").get_json()
    check("health admits it is open", health["locked"] is False)
    check("and the household is readable by anyone",
          client.get("/api/day?scenario=live").status_code == 200)


def test_the_day_knows_whether_it_is_today():
    section("today and every other day")
    from datetime import datetime, timezone
    from stillwatch.clock import local_date

    client, _ = live_app({})
    today = client.get("/api/day?scenario=live").get_json()
    check("today is marked as today", today["is_today"] is True, str(today["is_today"]))
    check("the per camera activity is still its own thing",
          isinstance(today["today"], dict))

    asked = local_date(datetime.now(timezone.utc)).replace(day=1).isoformat()
    past = client.get("/api/day?scenario=live&on=" + asked).get_json()
    check("a day that has been asked for is the day that comes back",
          past["day"] == asked, past["day"])
    if past["day"] != local_date(datetime.now(timezone.utc)).isoformat():
        check("and a finished day is not marked as today", past["is_today"] is False,
              str(past["is_today"]))
    else:
        check("and a finished day is not marked as today", True)


def test_the_headers_are_set():
    section("what every response carries")
    client, _ = live_app({})
    page = client.get("/")
    check("the page cannot be framed", page.headers.get("X-Frame-Options") == "DENY")
    check("content types are not guessed",
          page.headers.get("X-Content-Type-Options") == "nosniff")
    check("there is a content security policy", "frame-ancestors 'none'"
          in page.headers.get("Content-Security-Policy", ""))
    check("api answers are never cached",
          client.get("/api/health").headers.get("Cache-Control") == "no-store")


def main():
    source = ReplaySource(DATA)
    if not source.scenarios():
        print("no replay data in %s. From the project folder run: python demo_data.py" % DATA)
        return 1

    client = create_app(source).test_client()

    section("pages and health")
    page = client.get("/")
    check("the dashboard page is served", page.status_code == 200 and b"Stillwatch" in page.data)
    for name in ("app.js", "style.css"):
        check("%s is served" % name, client.get("/static/" + name).status_code == 200)

    health = client.get("/api/health").get_json()
    check("health reports ok", health["ok"] is True)
    check("health names the source", health["source"] == "replay")

    section("scenarios")
    listing = client.get("/api/scenarios").get_json()
    keys = [entry["key"] for entry in listing["scenarios"]]
    check("every scenario is listed", {"fall", "away", "camera_offline"} <= set(keys), str(keys))
    check("the persona is named", listing["persona"] == source.persona(), listing["persona"])

    section("a replayed day")
    day = client.get("/api/day?scenario=fall").get_json()
    check("a full day of readings at five minutes", len(day["readings"]) == 288,
          str(len(day["readings"])))
    check("readings run from midnight in order",
          day["readings"][0]["minute"] == 0
          and all(a["minute"] < b["minute"] for a, b in zip(day["readings"], day["readings"][1:])))
    check("every device is described", len(day["devices"]) == len(source.roster()),
          str(len(day["devices"])))
    check("the rhythm covers every device and hour",
          all(len(row) == 24 for row in day["baseline"]["rhythm"].values()))
    check("tolerated quiet covers every hour", len(day["baseline"]["quiet"]) == 24)
    check("habits carry their window",
          all("low" in a and "high" in a for a in day["baseline"]["anchors"]))
    check("the ladder limits are shared with the page",
          set(day["ladder"]) == {"quiet_at", "concern_at", "alert_at"})

    states = {reading["state"] for reading in day["readings"]}
    check("the collapse reaches an alert", "ALERT" in states, str(states))

    away = client.get("/api/day?scenario=away").get_json()
    away_states = {reading["state"] for reading in away["readings"]}
    check("the outing never alerts", "ALERT" not in away_states, str(away_states))
    check("the outing is shown as out", "AWAY" in away_states)

    kinds = [notice["kind"] for notice in day["notifications"]]
    check("the collapse day carries the messages it would send", kinds[:2] == ["concern", "alert"],
          str(kinds[:3]))
    check("the outing day sends nothing", away["notifications"] == [], str(away["notifications"]))

    offline = client.get("/api/day?scenario=camera_offline").get_json()
    check("the day's outage is reported",
          any(span["device_id"] == "kitchen" for span in offline["outages"]))

    section("refusing bad input")
    check("an unknown scenario is not found",
          client.get("/api/day?scenario=nope").status_code == 404)
    check("a path is not accepted as a scenario",
          client.get("/api/day?scenario=../data/manifest").status_code == 404)
    check("a missing scenario is not found", client.get("/api/day").status_code == 404)
    bad = client.get("/api/assess?scenario=fall&at=yesterday")
    check("a malformed time is rejected", bad.status_code == 400, str(bad.status_code))
    check("errors come back as json", "error" in bad.get_json())

    for test in (
        test_the_gate,
        test_the_gate_cannot_be_sat_on,
        test_a_forged_cookie_is_not_a_session,
        test_an_answer_carries_a_name_and_tells_everyone,
        test_an_open_service_says_so,
        test_the_day_knows_whether_it_is_today,
        test_the_headers_are_set,
    ):
        test()

    section("a single assessment")
    one = client.get("/api/assess?scenario=fall&at=2026-09-20T12:00:00Z").get_json()
    check("an assessment at noon on the collapse day is an alert", one["state"] == "ALERT",
          one["state"])
    check("it carries its reasons", bool(one["reasons"]))

    print("\n%d checks passed, %d failed" % (PASSED, len(FAILED)))
    if FAILED:
        for label in FAILED:
            print("  failed: %s" % label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
