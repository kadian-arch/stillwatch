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

from flask import Flask, abort, jsonify, make_response, request, send_from_directory
from markupsafe import escape

from . import access

from .clock import (
    clock,
    local_hour,
    household_tz,
    local_date,
    midnight_before,
    midnight_on,
    offset_minutes,
)
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
from .notify import MemoryChannel, Notifier, announce_answer
from .ring import RingClient, RingError, normalise_many, verify_signature
from .rhythm import ANY_INTERIOR, WINDOWS, daytype_of, human_duration, learn

WEB = Path(__file__).resolve().parent / "web"
STEP_MINUTES = 5
DAY_MINUTES = 24 * 60

# Sent on every response. None of it replaces the gate in access.py; it closes
# the gaps around it, so the page cannot be framed by somebody else's site and
# the browser never guesses at a content type.
# How long without a camera report or a judgement before health says so. Long
# enough that it is not an occupancy signal in disguise: a house is routinely
# quiet for an hour, so a boolean that flipped on the hour would tell a caller
# roughly what the timestamp used to.
FEED_SILENT_HOURS = 6

SECURITY_HEADERS = {
    # A year, including subdomains, and offered for preloading. The dashboard
    # is served over TLS and nothing about it should ever be attempted in
    # clear, including the first request of a session, which is the one a
    # redirect cannot protect.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'self'"
    ),
}


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
            "target_day": local_date(datetime.now(timezone.utc)).isoformat(),
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
    where = ("Up and about" if anchor.device_id == ANY_INTERIOR
             else "The %s camera" % roster.name(anchor.device_id))
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
        "where": where,
        "daytype": anchor.daytype,
        "sentence": "%s by %s, on %d%% of %d %s days" % (
            where,
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


def build_day(source, key, on=None, confirmed=(), answers=(), last_contact=None):
    """Everything the dashboard needs for one day, computed once.

    `on` asks for a particular date. A caregiver wants to know what yesterday
    looked like, and a day that has already finished is walked end to end
    rather than stopping at the present.

    `last_contact` only applies to today. It is when something was last
    delivered to the service, which is the difference between a house where
    nobody moved and a feed that stopped. Carrying it into a finished day would
    be claiming the connection was alive then because it is alive now.
    """
    meta = source.describe(key) or {"key": key, "title": key}
    events = source.events(key)
    roster = source.roster(key)
    live = bool(meta.get("live", getattr(source, "kind", "replay") == "live"))

    now = datetime.now(timezone.utc)
    if live:
        day = on or local_date(now)
    else:
        target = meta.get("target_day")
        day = on or (date.fromisoformat(target) if target else last_covered_day(events))
    # Only today is still being written. Every other day is finished.
    is_today = live and day == local_date(now)
    said = {row["episode"]: row for row in answers}

    midnight = midnight_on(day)
    daytype = daytype_of(midnight)

    baseline = learn(events, roster, until=midnight, confirmed=confirmed)
    # A replay walks the whole day. Live stops at the present, because the rest
    # of today has not happened yet.
    last = midnight + timedelta(minutes=DAY_MINUTES - STEP_MINUTES)
    if is_today:
        last = min(last, now)
    readings = walk(events, baseline, roster, midnight, last, STEP_MINUTES,
                    last_contact=last_contact if is_today else None,
                    answered=said.get)

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
        link=lambda reading: "/?scenario=%s&t=%s" % (key, clock(reading.at)),
        answered=said.get,
    )
    for reading in readings:
        notifier.observe(reading)

    activity, dings = _day_activity(events, roster, midnight)
    quiet = [baseline.tolerated_quiet(daytype, hour) for hour in range(24)]

    return {
        "scenario": meta,
        "persona": source.persona(),
        "source": getattr(source, "kind", "replay"),
        "live": live,
        "as_of": (now if is_today else midnight + timedelta(minutes=DAY_MINUTES)).isoformat(),
        # Not "today": that key already carries this day's activity per camera.
        "is_today": is_today,
        "answers": said,
        "day": day.isoformat(),
        "weekday": midnight.strftime("%A"),
        "timezone": str(household_tz()),
        "utc_offset_minutes": offset_minutes(midnight),
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
        "today": activity,
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


def create_app(source, store=None, webhook_secret=None, ring=None,
               channels=None, environ=None, narrator=None):
    app = Flask(__name__, static_folder=None)
    cache = {}
    lock = threading.Lock()
    live = getattr(source, "kind", "replay") == "live"
    environ = os.environ if environ is None else environ
    channels = list(channels or [])

    passcode = access.passcode_for(environ)
    members = access.members_for(environ)
    key = access.signing_key(environ)
    # Named people if there are any, one shared code if not, open if neither.
    # A code with nothing to sign cookies with would lock out the household
    # along with everybody else, so the gate only goes up when both exist.
    mode = "members" if members else ("shared" if passcode else "open")
    locked = bool(key) and mode != "open"
    if not locked:
        mode = "open"
    sign_in_throttle = access.Throttle(limit=8, seconds=15 * 60)
    answer_throttle = access.Throttle(limit=30, seconds=60)

    def viewer():
        """Who is asking, or None if nobody has signed in."""
        if not locked:
            return None
        return access.read(request.cookies.get(access.COOKIE), key)

    def allowed():
        return (not locked) or viewer() is not None

    def demo_only(scenario):
        """Recorded days stay open. They are not anybody's home."""
        entry = source.describe(scenario) or {}
        return bool(entry.get("demo")) or not entry.get("live", live)

    @app.after_request
    def harden(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    def known(key):
        return key in {entry["key"] for entry in source.scenarios()}

    def bundle(scenario, on=None):
        # Today keeps happening, so it is never cached. A day that has finished
        # cannot change, so it is worked out once.
        confirmed = tuple(store.confirmed_quiet()) if store is not None else ()
        said = tuple(store.answers()) if store is not None else ()
        contact = store.last_delivery_at() if store is not None else None
        if (live and scenario == "live"
                and (on is None or on == local_date(datetime.now(timezone.utc)))):
            return build_day(source, scenario, confirmed=confirmed, answers=said,
                             last_contact=contact)
        token = (scenario, on.isoformat() if on else None)
        with lock:
            if token not in cache:
                cache[token] = build_day(source, scenario, on, confirmed=confirmed,
                                         answers=said)
            return cache[token]

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
        # A link preview needs absolute addresses. Relative ones are in the
        # file because the page has to work from a folder, from localhost and
        # from a domain, so the two that must be absolute are made absolute
        # here, against whatever host the request actually arrived on.
        page = (WEB / "index.html").read_text(encoding="utf-8")
        root = request.url_root.rstrip("/")
        page = page.replace('property="og:url" content="/"',
                            'property="og:url" content="%s/"' % root)
        page = page.replace('property="og:image" content="/static/og.png"',
                            'property="og:image" content="%s/static/og.png"' % root)
        answer = make_response(page)
        answer.headers["Content-Type"] = "text/html; charset=utf-8"
        return answer

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(WEB, name)

    @app.get("/api/session")
    def session():
        return jsonify({"locked": locked, "mode": mode, "name": viewer()})

    @app.post("/api/session")
    def sign_in():
        if not locked:
            return jsonify({"locked": False, "mode": mode, "name": None}), 200
        if not sign_in_throttle.allow(access.caller(request)):
            return jsonify({"error": "too many attempts, wait a few minutes"}), 429

        body = request.get_json(silent=True) or request.form or {}
        given = access.clean_name(body.get("name"))
        if not given:
            abort(400, description="a name is needed, so the family can see who answered")

        if mode == "members":
            name = access.check_member(members, given, body.get("passcode"))
        else:
            name = given if access.matches(body.get("passcode"), passcode) else None

        if not name:
            # Deliberately the same answer whatever was wrong with it, so that
            # it never says whether the name or the code was the problem.
            return jsonify({"error": "that name and code do not match"}), 401

        reply = make_response(jsonify({"locked": True, "mode": mode, "name": name}))
        reply.set_cookie(
            access.COOKIE,
            access.issue(name, key),
            max_age=access.SESSION_DAYS * 24 * 3600,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure,
            path="/",
        )
        return reply

    @app.delete("/api/session")
    def sign_out():
        reply = make_response(jsonify({"locked": locked, "mode": mode, "name": None}))
        reply.delete_cookie(access.COOKIE, path="/")
        return reply

    @app.get("/api/health")
    def health():
        payload = {
            "ok": True,
            "source": getattr(source, "kind", "replay"),
            "scenarios": len(source.scenarios()),
            "webhook": bool(store is not None and webhook_secret),
            "ring_linked": bool(store is not None and store.is_linked()),
            "locked": locked,
            "mode": mode,
            "name": viewer(),
        }
        if store is not None:
            last = store.last_event_at()
            delivered = store.last_delivery_at()
            watched = store.load_state("watcher") or {}
            payload["stored_events"] = store.count()
            payload["watching"] = bool(watched.get("at"))

            # When somebody last moved in this house is the occupancy record,
            # which is the thing the sign in exists to keep. Handing out the
            # timestamp let anyone poll this endpoint once a minute and read
            # off when the house went quiet and when it woke, which is the
            # whole of what the wall was built to stop, offered by the one
            # endpoint whose job was to say the service is running.
            #
            # Health is still answerable without it. A feed that has stopped
            # and a watcher that has stopped are both yes or no questions.
            fresh = datetime.now(timezone.utc) - timedelta(hours=FEED_SILENT_HOURS)
            payload["feed_recent"] = bool(last and last >= fresh)
            payload["judging_recently"] = bool(
                watched.get("at")
                and parse_timestamp(watched["at"]) >= fresh)

            if allowed():
                payload["last_event_at"] = last.isoformat() if last else None
                payload["last_delivery_at"] = (
                    delivered.isoformat() if delivered else None)
                payload["last_tick_at"] = watched.get("at")
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

        # Recorded before the payload is even read. A delivery we could not
        # understand still proves the connection is alive.
        store.note_delivery(datetime.now(timezone.utc))

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

    OUTCOMES = {
        "fine": "Checked, and all is well",
        "away": "She was out, not still",
        "expected": "This is normal for her now",
        "helped": "Something was wrong and has been dealt with",
    }

    @app.get("/api/answers")
    def answers():
        if store is None:
            return jsonify({"answers": [], "outcomes": OUTCOMES})
        return jsonify({"answers": store.answers(), "outcomes": OUTCOMES})

    @app.post("/api/answer")
    def answer():
        """A caregiver saying what they found. This is what ends an episode.

        Until somebody answers, the only thing that stops Stillwatch worrying
        is movement, which is no use to a daughter who has already phoned and
        found her mother perfectly well.
        """
        if store is None:
            abort(503, description="no event store configured")

        body = request.get_json(silent=True) or {}
        episode = str(body.get("episode") or "").strip()
        outcome = str(body.get("outcome") or "").strip()
        note = (body.get("note") or "").strip()[:500] or None

        if not allowed():
            abort(401, description="sign in before answering for this household")
        if not answer_throttle.allow(access.caller(request)):
            return jsonify({"error": "too many answers, slow down"}), 429

        if not episode:
            abort(400, description="which stretch of quiet is this about?")
        if outcome not in OUTCOMES:
            abort(400, description="outcome must be one of %s" % ", ".join(sorted(OUTCOMES)))

        try:
            began = parse_timestamp(episode)
        except ValueError:
            abort(400, description="episode must be an ISO 8601 timestamp")

        now = datetime.now(timezone.utc)
        seconds = None
        if outcome == "expected":
            # Only a stretch that has actually finished teaches us anything.
            latest = store.last_event_at(kinds=("motion", "ding"))
            seconds = max(0.0, ((latest or now) - began).total_seconds())

        by = viewer()
        store.save_answer(
            episode, outcome, now, note=note, by=by,
            daytype=daytype_of(began), hour=local_hour(began), seconds=seconds,
        )
        cache.clear()

        # Everybody who would have been chased about this hears that it is
        # handled, and by whom. A family of three should not each phone.
        told = announce_answer(channels, source.persona(), outcome, by, now,
                               note=note, episode=episode, narrator=narrator)
        return jsonify({"episode": episode, "outcome": outcome, "by": by,
                        "means": OUTCOMES[outcome], "told": told}), 200

    @app.get("/api/day")
    def day():
        scenario = request.args.get("scenario", "")
        if not known(scenario):
            abort(404)
        if not demo_only(scenario) and not allowed():
            abort(401, description="sign in to see this household")

        asked = request.args.get("on", "").strip()
        on = None
        if asked:
            try:
                on = date.fromisoformat(asked)
            except ValueError:
                abort(400, description="on must be a date like 2026-09-25")
            if on > local_date(datetime.now(timezone.utc)):
                abort(400, description="that day has not happened yet")
            # A day before the house had any history is as meaningless as one
            # after today, and was answered with an empty day rather than an
            # explanation. Refusing only one end of the range is the kind of
            # inconsistency that reads as nobody having thought about it.
            earliest = store.first_event_at() if store is not None else None
            if earliest is not None and on < local_date(earliest):
                abort(400, description="nothing was recorded here before %s"
                      % local_date(earliest).isoformat())
        return jsonify(bundle(scenario, on))

    @app.get("/api/assess")
    def assess_at():
        scenario = request.args.get("scenario", "")
        if not known(scenario):
            abort(404)
        if not demo_only(scenario) and not allowed():
            abort(401, description="sign in to see this household")
        raw = request.args.get("at")
        try:
            moment = parse_timestamp(raw) if raw else datetime.now(timezone.utc)
        except ValueError:
            abort(400, description="at must be an ISO 8601 timestamp")

        events = source.events(scenario)
        roster = source.roster(scenario)
        midnight = midnight_before(moment)
        told = tuple(store.confirmed_quiet()) if store is not None else ()
        baseline = learn(events, roster, until=midnight, confirmed=told)
        contact = store.last_delivery_at() if store is not None else None
        answered = store.answer_for if store is not None else None
        return jsonify(assess(events, baseline, roster, moment,
                              last_contact=contact, answered=answered).to_dict())

    return app
