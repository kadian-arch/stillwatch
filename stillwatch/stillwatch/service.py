"""Web service: the dashboard, and the API it reads.

The service never knows where events come from. A source hands it events and a
device roster. The replay source reads files written by the simulator, and a
Ring source will hand over the same shapes from the API.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from markupsafe import escape

from .model import (
    DING,
    MOTION,
    last_covered_day,
    load_events,
    load_roster,
    outage_spans,
    parse_timestamp,
)
from .monitor import ALERT_AT, CONCERN_AT, QUIET_AT, assess, walk
from .notify import MemoryChannel, Notifier
from .ring import RingClient, RingError, normalise_many, verify_signature
from .rhythm import ANY_INTERIOR, WINDOWS, daytype_of, human_duration, learn

WEB = Path(__file__).resolve().parent / "web"
STEP_MINUTES = 5
DAY_MINUTES = 24 * 60


class ReplaySource:
    kind = "replay"

    def __init__(self, folder):
        self.folder = Path(folder)

    def _index(self):
        path = self.folder / "scenarios.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        keys = sorted(item.stem for item in self.folder.glob("*.jsonl"))
        return {"persona": None, "scenarios": [{"key": key, "title": key} for key in keys]}

    def persona(self):
        return self._index().get("persona") or "The household"

    def scenarios(self):
        available = {item.stem for item in self.folder.glob("*.jsonl")}
        return [entry for entry in self._index()["scenarios"] if entry["key"] in available]

    def describe(self, key):
        for entry in self.scenarios():
            if entry["key"] == key:
                return entry
        return None

    def events(self, key):
        return load_events(self.folder / ("%s.jsonl" % key))

    def roster(self, key=None):
        return load_roster(self.folder / "manifest.json")


class LiveSource:
    """Real events, out of the store the webhook writes to."""

    kind = "live"

    def __init__(self, store, person="The household"):
        self.store = store
        self.person = person

    def persona(self):
        return self.person

    def scenarios(self):
        return [{
            "key": "live",
            "title": "Live now",
            "description": "Today, from the cameras themselves.",
            "expectation": None,
            "target_day": datetime.now(timezone.utc).date().isoformat(),
        }]

    def describe(self, key):
        return self.scenarios()[0] if key == "live" else None

    def events(self, key):
        return self.store.events()

    def roster(self, key=None):
        return self.store.roster()


class CompositeSource:
    """Real events, with the demonstration days still reachable beside them.

    A home that has just been connected has nothing to show, and a blank
    dashboard is a poor way to explain what the product does. The recorded
    days stay available so it can be seen working before its own history
    exists. They are marked as demonstrations wherever they are offered."""

    kind = "live"

    def __init__(self, live, demo):
        self.live = live
        self.demo = demo

    def persona(self):
        return self.live.persona()

    def scenarios(self):
        entries = [dict(entry, live=True) for entry in self.live.scenarios()]
        for entry in self.demo.scenarios():
            entries.append(dict(entry, live=False, demo=True))
        return entries

    def describe(self, key):
        for entry in self.scenarios():
            if entry["key"] == key:
                return entry
        return None

    def _pick(self, key):
        return self.live if key == "live" else self.demo

    def events(self, key):
        return self._pick(key).events(key)

    def roster(self, key=None):
        return self._pick(key).roster(key)


def _minute(moment, midnight):
    return round((moment - midnight).total_seconds() / 60.0, 2)


def _anchor_view(anchor, roster):
    where = ("up and about" if anchor.device_id == ANY_INTERIOR
             else "in the %s" % roster.name(anchor.device_id))
    low, high = next(((lo, hi) for name, lo, hi in WINDOWS if name == anchor.window), (0, DAY_MINUTES))
    return {
        "device_id": anchor.device_id,
        "window": anchor.window,
        "low": low,
        "high": high,
        "usual": anchor.clock(),
        "minute": anchor.median_minute,
        "spread_minutes": anchor.spread_minutes,
        "hit_rate": anchor.hit_rate,
        "observed_days": anchor.observed_days,
        "sentence": "%s by %s, on %d%% of %d %s days" % (
            where[0].upper() + where[1:],
            anchor.clock(),
            round(anchor.hit_rate * 100),
            anchor.observed_days,
            anchor.daytype,
        ),
    }


def _day_activity(events, roster, midnight):
    close = midnight + timedelta(days=1)
    today = {device.device_id: [] for device in roster}
    dings = []
    for event in events:
        if not midnight <= event.at < close:
            continue
        if event.kind == MOTION and event.device_id in today:
            today[event.device_id].append(_minute(event.at, midnight))
        elif event.kind == DING:
            dings.append({"device_id": event.device_id, "minute": _minute(event.at, midnight)})
    return today, dings


def _day_outages(events, midnight):
    close = midnight + timedelta(days=1)
    spans = []
    for device_id, start, end in outage_spans(events):
        finish = end or close
        if finish <= midnight or start >= close:
            continue
        spans.append({
            "device_id": device_id,
            "from": max(0.0, _minute(start, midnight)),
            "to": min(float(DAY_MINUTES), _minute(finish, midnight)),
        })
    return spans


def build_day(source, key):
    """Everything the dashboard needs for one day, computed once."""
    meta = source.describe(key) or {"key": key, "title": key}
    events = source.events(key)
    roster = source.roster(key)
    live = bool(meta.get("live", getattr(source, "kind", "replay") == "live"))

    if live:
        now = datetime.now(timezone.utc)
        day = now.date()
    else:
        now = None
        target = meta.get("target_day")
        day = date.fromisoformat(target) if target else last_covered_day(events)

    midnight = datetime.combine(day, time(0, 0), tzinfo=timezone.utc)
    daytype = daytype_of(midnight)

    baseline = learn(events, roster, until=midnight)
    # A replay walks the whole day. Live stops at the present, because the rest
    # of today has not happened yet.
    last = midnight + timedelta(minutes=DAY_MINUTES - STEP_MINUTES)
    if live:
        last = min(last, now)
    readings = walk(events, baseline, roster, midnight, last, STEP_MINUTES)

    views = []
    for reading in readings:
        view = reading.to_dict()
        view["minute"] = _minute(reading.at, midnight)
        view["began_minute"] = (_minute(reading.silence_began, midnight)
                                if reading.silence_began else None)
        view["silence_text"] = human_duration(reading.silence_seconds)
        view["threshold_text"] = human_duration(reading.threshold_seconds)
        views.append(view)

    outbox = MemoryChannel()
    notifier = Notifier(
        source.persona(),
        [outbox],
        link=lambda reading: "/?scenario=%s&t=%s" % (key, reading.at.strftime("%H:%M")),
    )
    for reading in readings:
        notifier.observe(reading)

    today, dings = _day_activity(events, roster, midnight)
    quiet = [baseline.tolerated_quiet(daytype, hour) for hour in range(24)]

    return {
        "scenario": meta,
        "persona": source.persona(),
        "source": getattr(source, "kind", "replay"),
        "live": live,
        "as_of": (now or midnight + timedelta(minutes=DAY_MINUTES)).isoformat(),
        "day": day.isoformat(),
        "weekday": midnight.strftime("%A"),
        "daytype": daytype,
        "step_minutes": STEP_MINUTES,
        "ladder": {"quiet_at": QUIET_AT, "concern_at": CONCERN_AT, "alert_at": ALERT_AT},
        "devices": [
            {"device_id": d.device_id, "name": d.name, "zone_class": d.zone_class}
            for d in roster
        ],
        "readings": views,
        "notifications": [
            {
                "minute": _minute(notice.at, midnight),
                "kind": notice.kind,
                "urgency": notice.urgency,
                "subject": notice.subject,
                "body": notice.body,
            }
            for notice in outbox.notices
        ],
        "today": today,
        "dings": dings,
        "outages": _day_outages(events, midnight),
        "baseline": {
            "days_observed": baseline.days_observed,
            "excluded_absences": baseline.excluded_absences,
            "rhythm": {
                d.device_id: [round(baseline.activity_rate(d.device_id, daytype, hour), 3)
                              for hour in range(24)]
                for d in roster
            },
            "quiet": quiet,
            "quiet_text": [human_duration(value) for value in quiet],
            "anchors": [
                _anchor_view(anchor, roster)
                for anchor in sorted(baseline.anchors_for(daytype), key=lambda a: a.median_minute)
            ],
        },
    }


LINK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Connect to Stillwatch</title>
<link rel="stylesheet" href="/static/style.css"></head><body>
<main class="layout" style="grid-template-columns:minmax(0,1fr);grid-template-areas:'now';max-width:560px">
<section class="card"><h2>Connect this home</h2>
<p class="headline">Stillwatch will watch for the silence, not for you.</p>
<p class="note">It reads when your cameras see movement and when they do not. It never
watches video, and it never stores a picture of anyone. You can disconnect it at any
time from the Ring app.</p>
<p style="margin-top:18px"><a class="play" href="%s">Continue</a></p>
</section></main></body></html>"""


def create_app(source, store=None, webhook_secret=None, ring=None):
    app = Flask(__name__, static_folder=None)
    cache = {}
    lock = threading.Lock()
    live = getattr(source, "kind", "replay") == "live"

    def known(key):
        return key in {entry["key"] for entry in source.scenarios()}

    def bundle(key):
        # Today keeps happening, so the live day is never cached. A recorded
        # day cannot change, so it is worked out once.
        if live and key == "live":
            return build_day(source, key)
        with lock:
            if key not in cache:
                cache[key] = build_day(source, key)
            return cache[key]

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return error

    @app.errorhandler(400)
    def bad_request(error):
        return jsonify({"error": getattr(error, "description", "bad request")}), 400

    @app.get("/")
    def index():
        return send_from_directory(WEB, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(WEB, name)

    @app.get("/api/health")
    def health():
        payload = {
            "ok": True,
            "source": getattr(source, "kind", "replay"),
            "scenarios": len(source.scenarios()),
            "webhook": bool(store is not None and webhook_secret),
            "ring_linked": bool(store is not None and store.is_linked()),
        }
        if store is not None:
            last = store.last_event_at()
            payload["stored_events"] = store.count()
            payload["last_event_at"] = last.isoformat() if last else None
        return jsonify(payload)

    def ring_client():
        if ring is not None:
            return ring
        client_id = os.environ.get("STILLWATCH_RING_CLIENT_ID", "").strip()
        client_secret = os.environ.get("STILLWATCH_RING_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            return None
        return RingClient(client_id, client_secret)

    @app.get("/ring/link")
    def ring_link():
        """Where Ring sends someone to authorise. Ring passes the address to
        return them to, and we never invent one of our own."""
        destination = request.args.get("redirect_uri") or request.args.get("state") or "/ring/linked"
        return LINK_PAGE % escape(destination)

    @app.get("/ring/linked")
    def ring_linked():
        connected = bool(store is not None and store.is_linked())
        return LINK_PAGE % "/" if not connected else (
            LINK_PAGE % "/").replace("Connect this home", "This home is connected")

    @app.post("/ring/token")
    def ring_token():
        """Ring posts the authorisation code here. We swap it at Ring's token
        endpoint and keep the result in the store, never on disk in the repo."""
        if store is None:
            abort(503, description="no event store configured")
        client = ring_client()
        if client is None:
            abort(503, description="no Ring client credentials configured")

        payload = request.get_json(silent=True) or request.form or {}
        code = payload.get("code") or payload.get("authorization_code")
        if not code:
            abort(400, description="no authorisation code in the request")

        try:
            client.exchange(code)
        except RingError as error:
            app.logger.error("token exchange failed: %s", error)
            return jsonify({"error": "token exchange failed"}), 502

        store.save_tokens(payload.get("account", "default"), client.access_token,
                          client.refresh_token, client.expires_at)
        return jsonify({"linked": True}), 200

    @app.post("/ring/events")
    def ring_events():
        """Ring posts here. It must be answered within five seconds, so this
        does nothing but check the signature and write the events down."""
        if store is None or not webhook_secret:
            abort(503, description="no event store configured")

        body = request.get_data()
        if not verify_signature(webhook_secret, body, request.headers.get("X-Signature", "")):
            app.logger.warning("rejected a webhook with a bad signature")
            return jsonify({"error": "bad signature"}), 401

        try:
            payload = json.loads(body.decode("utf-8") or "{}")
            events = normalise_many(payload)
        except (ValueError, RingError) as error:
            # Answer 200 anyway. Ring cannot fix a payload we cannot read by
            # sending it again, and a retry loop helps nobody.
            app.logger.warning("unusable webhook payload: %s", error)
            return jsonify({"stored": 0, "ignored": 1}), 200

        stored = store.add_many(events)
        return jsonify({"stored": stored, "received": len(events)}), 200

    @app.get("/api/scenarios")
    def scenarios():
        return jsonify({"persona": source.persona(), "scenarios": source.scenarios()})

    @app.get("/api/day")
    def day():
        key = request.args.get("scenario", "")
        if not known(key):
            abort(404)
        return jsonify(bundle(key))

    @app.get("/api/assess")
    def assess_at():
        key = request.args.get("scenario", "")
        if not known(key):
            abort(404)
        raw = request.args.get("at")
        try:
            moment = parse_timestamp(raw) if raw else datetime.now(timezone.utc)
        except ValueError:
            abort(400, description="at must be an ISO 8601 timestamp")

        events = source.events(key)
        roster = source.roster(key)
        midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        baseline = learn(events, roster, until=midnight)
        return jsonify(assess(events, baseline, roster, moment).to_dict())

    return app
