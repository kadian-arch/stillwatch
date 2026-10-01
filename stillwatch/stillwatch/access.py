"""Who may look at a household, and who may answer for it.

A dashboard that shows when somebody is home, when they sleep and when the
house is empty is a useful thing for the family and a gift to anybody else.
The answer buttons are worse: whoever can press one can silence a real alarm.
Neither belongs on an open address.

The gate is deliberately small. A household sets one passcode. Anybody holding
it signs in with a name, and the name is carried on every answer so the family
can see who said the house was fine. There is no account to create, no address
to collect, and nothing to leak but the passcode itself, which the household
can change by editing one setting.

Sessions are a signed cookie rather than rows in a table, so they survive a
restart, cost no storage, and cannot be read or extended by whoever holds one.
With no passcode set the service runs open and says so on the page, because a
lock nobody knows about is worse than no lock at all.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timedelta, timezone

COOKIE = "stillwatch_session"
SESSION_DAYS = 14
NAME_LIMIT = 40

# Long enough that guessing is hopeless, short enough to read down a phone.
MIN_PASSCODE = 6


def passcode_for(environ):
    return (environ.get("STILLWATCH_PASSCODE") or "").strip()


def members_for(environ):
    """The people allowed in, each with a code of their own.

    Read from one setting, as `Lucie:7F3K2Q, KAD:9ZP4MX`. One shared code
    tells you a household answered; a code each tells you which of them did,
    which is the difference between a record and a guess. Where a family wants
    the simpler arrangement, the shared passcode is still there.

    Keyed on a case folded name so that signing in is forgiving, and the name
    that gets recorded is always the spelling the household chose, never the
    spelling that was typed.
    """
    raw = (environ.get("STILLWATCH_MEMBERS") or "").strip()
    people = {}
    for entry in raw.split(","):
        name, _, code = entry.strip().partition(":")
        name, code = clean_name(name), code.strip()
        if name and code:
            people[name.casefold()] = (name, code)
    return people


def check_member(people, name, code):
    """The household's own spelling of that person's name, or None.

    A name nobody holds is compared against a decoy of the same shape, so the
    time taken to refuse it says nothing about whether that person exists.
    """
    found = people.get(clean_name(name).casefold())
    if not found:
        matches(code, "x" * 32)
        return None
    canonical, expected = found
    return canonical if matches(code, expected) else None


def signing_key(environ):
    """What session cookies are signed with.

    Its own setting if there is one. Otherwise the webhook secret, which is
    already present wherever this runs and is never sent to a browser. A key
    derived from it is used rather than the secret itself, so a session cookie
    can never be turned back into something that would pass as Ring.
    """
    own = (environ.get("STILLWATCH_SESSION_SECRET") or "").strip()
    if own:
        return own.encode("utf-8")
    fallback = (environ.get("STILLWATCH_RING_WEBHOOK_SECRET") or "").strip()
    if not fallback:
        return None
    return hashlib.sha256(b"stillwatch-session:" + fallback.encode("utf-8")).digest()


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text):
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(key, payload):
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def clean_name(value):
    """A display name, not a field anybody should be able to smuggle through."""
    text = " ".join(str(value or "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:NAME_LIMIT].strip()


def issue(name, key, now=None, days=SESSION_DAYS):
    now = now or datetime.now(timezone.utc)
    body = json.dumps(
        {"name": clean_name(name), "exp": (now + timedelta(days=days)).timestamp()},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "%s.%s" % (_b64(body), _sign(key, body))


def read(token, key, now=None):
    """The name inside a cookie, or None if it is not ours or has run out."""
    if not token or not key or "." not in token:
        return None
    head, _, tail = token.partition(".")
    try:
        body = _unb64(head)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_sign(key, body), tail):
        return None
    try:
        claim = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    if float(claim.get("exp") or 0) <= now.timestamp():
        return None
    return clean_name(claim.get("name")) or None


def matches(given, expected):
    """Constant time, so the answer cannot be measured a character at a time."""
    if not expected:
        return False
    return hmac.compare_digest(str(given or "").strip(), expected)


class Throttle:
    """A small fixed window counter, per caller.

    The passcode is the only thing between a stranger and the household, so the
    sign in route must not be something you can sit on and try. This runs in
    the process rather than in a store because it has to be cheap enough to
    call on every request, and because a single dyno is the shape this is
    deployed in. More than one and each keeps its own count, which loosens the
    limit without removing it.
    """

    def __init__(self, limit, seconds):
        self.limit = limit
        self.seconds = seconds
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key, now=None):
        now = now if now is not None else time.monotonic()
        with self._lock:
            if len(self._hits) > 4096:
                self._hits = {k: v for k, v in self._hits.items()
                              if now - v[0] < self.seconds}
            started, count = self._hits.get(key, (now, 0))
            if now - started >= self.seconds:
                started, count = now, 0
            count += 1
            self._hits[key] = (started, count)
            return count <= self.limit


def caller(request):
    """Who is asking, as well as it can be known behind a proxy.

    Heroku and Cloudflare both put the real address at the front of
    X-Forwarded-For and append their own. Only the first entry is used, and
    only for counting requests, never for a decision about trust.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.remote_addr or "unknown")[:64]
