"""Checks on the dashboard service. Run directly, exits non zero on failure.

Needs the replay data: from the project folder run python demo_data.py first.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stillwatch.service import ReplaySource, create_app

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
    check("the persona is named", listing["persona"] == "Margaret", listing["persona"])

    section("a replayed day")
    day = client.get("/api/day?scenario=fall").get_json()
    check("a full day of readings at five minutes", len(day["readings"]) == 288,
          str(len(day["readings"])))
    check("readings run from midnight in order",
          day["readings"][0]["minute"] == 0
          and all(a["minute"] < b["minute"] for a, b in zip(day["readings"], day["readings"][1:])))
    check("every device is described", len(day["devices"]) == 6)
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
