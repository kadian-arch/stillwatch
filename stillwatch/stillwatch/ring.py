"""The only place that knows what Ring calls things.

Ring says motion_detected where the engine says motion, and wraps payloads the
way JSON:API does. Everything downstream works on our own Event, so this module
is the whole cost of swapping the simulator for real hardware.

Field names follow the Ring Partner API documentation. Where the docs leave a
shape open, several spellings are accepted rather than one guessed, because a
webhook that arrives at three in the morning is a poor time to discover that a
key was named differently.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .model import DING, Device, Event, INTERIOR, MOTION, OFFLINE, ONLINE, TRANSIT, parse_timestamp

TOKEN_URL = "https://oauth.ring.com/oauth/token"
API_BASE = "https://api.amazonvision.com/v1"
SCOPE = "ava.v1:read"

SIGNATURE_HEADER = "X-Signature"
SIGNATURE_PREFIX = "sha256="

# Pagination is undocumented, so there is a hard stop rather than a promise.
MAX_HISTORY_PAGES = 50

# Ring event types, mapped onto what the engine reasons about. Anything absent
# from this table is ignored on purpose rather than by accident.
KINDS = {
    "motion_detected": MOTION,
    "motion": MOTION,
    "button_press": DING,
    "ding": DING,
    "device_online": ONLINE,
    "device_offline": OFFLINE,
}

IGNORED_KINDS = (
    "device_added",
    "device_removed",
    "app_integration_added",
    "app_integration_removed",
    "subscription_activated",
    "subscription_deactivated",
)

ID_KEYS = ("event_id", "id", "eventId")
DEVICE_KEYS = ("device_id", "deviceId", "doorbot_id")
KIND_KEYS = ("kind", "event_type", "eventType", "type")
TIME_KEYS = ("created_at", "createdAt", "occurred_at", "occurredAt", "timestamp", "event_time")

# Words in a device name that mean it watches a way in or out. Everything else
# is treated as inside the home, which is the safer default: a door mistaken
# for a room only costs us an explanation, while a room mistaken for a door
# would let a silence be explained away.
DOOR_WORDS = ("door", "porch", "entry", "entrance", "gate", "driveway", "garage", "yard", "patio")


class RingError(Exception):
    pass


def classify(name):
    lowered = (name or "").lower()
    return TRANSIT if any(word in lowered for word in DOOR_WORDS) else INTERIOR


def _first(record, keys):
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def unwrap(payload):
    """Ring follows JSON:API, so the event may sit under data, with attributes."""
    record = payload
    if isinstance(record, dict) and isinstance(record.get("data"), dict):
        record = record["data"]
    if isinstance(record, dict) and isinstance(record.get("attributes"), dict):
        merged = {key: value for key, value in record.items() if key != "attributes"}
        merged.update(record["attributes"])
        record = merged
    return record


def normalise_ring(payload):
    """One Ring event as an engine Event, or None if it is not one we act on."""
    record = unwrap(payload)
    if not isinstance(record, dict):
        raise RingError("event payload was not an object")

    raw_kind = _first(record, KIND_KEYS)
    if raw_kind is None:
        raise RingError("event had no type")
    if raw_kind in IGNORED_KINDS:
        return None
    kind = KINDS.get(raw_kind)
    if kind is None:
        return None

    device_id = _first(record, DEVICE_KEYS)
    if device_id is None:
        relationships = record.get("relationships") or {}
        device = (relationships.get("device") or {}).get("data") or {}
        device_id = device.get("id")
    if device_id is None:
        raise RingError("event named no device")

    stamp = _first(record, TIME_KEYS)
    if stamp is None:
        raise RingError("event had no timestamp")
    at = parse_timestamp(str(stamp))

    event_id = _first(record, ID_KEYS)
    if event_id is None:
        # Without an id from Ring, build a stable one, so a redelivery of the
        # same event still collides in the store instead of counting twice.
        seed = "%s|%s|%s" % (device_id, raw_kind, at.isoformat())
        event_id = "ring_" + hashlib.sha256(seed.encode()).hexdigest()[:24]

    return Event(event_id=str(event_id), device_id=str(device_id), kind=kind, at=at)


def normalise_many(payload):
    """A webhook or history page, which may hold one event or a list."""
    body = payload.get("data") if isinstance(payload, dict) else None
    records = body if isinstance(body, list) else [payload]
    events = []
    for record in records:
        event = normalise_ring(record)
        if event is not None:
            events.append(event)
    return events


def next_link(payload):
    """The next page, if the reply offers one.

    Only an address on Ring's own API is followed. A next link is data from
    the network, and following it anywhere it points would let a compromised
    or mistaken reply send our access token to somebody else.
    """
    if not isinstance(payload, dict):
        return None
    candidates = []
    links = payload.get("links")
    if isinstance(links, dict):
        candidates.append(links.get("next"))
    meta = payload.get("meta")
    if isinstance(meta, dict):
        candidates.extend([meta.get("next"), meta.get("next_url")])

    for candidate in candidates:
        if not candidate or not isinstance(candidate, str):
            continue
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme == "https" and parsed.netloc == urllib.parse.urlparse(API_BASE).netloc:
            return candidate
    return None


def device_from_ring(payload):
    record = unwrap(payload)
    device_id = _first(record, DEVICE_KEYS) or record.get("id")
    if device_id is None:
        raise RingError("device had no id")
    name = record.get("name") or record.get("description") or str(device_id)
    return Device(device_id=str(device_id), name=str(name), zone_class=classify(name))


def sign(secret, body):
    digest = hmac.new(_as_bytes(secret), _as_bytes(body), hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_signature(secret, body, header):
    """Constant time check of Ring's X-Signature header."""
    if not secret or not header:
        return False
    return hmac.compare_digest(sign(secret, body), header.strip())


def _as_bytes(value):
    return value.encode("utf-8") if isinstance(value, str) else value


def _post_form(url, fields, timeout):
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    return _send(request, timeout)


def _get_json(url, token, timeout):
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/json")
    return _send(request, timeout)


def _send(request, timeout):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise RingError("%s said %s: %s" % (request.full_url, error.code, detail))
    except urllib.error.URLError as error:
        raise RingError("could not reach %s: %s" % (request.full_url, error.reason))


class RingClient:
    """Talks to Ring over plain HTTP, because there is no official SDK.

    The transport is injectable so the tests never touch the network.
    """

    def __init__(self, client_id, client_secret, refresh_token=None, access_token=None,
                 expires_at=None, transport=None, timeout=10):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.expires_at = expires_at
        self.timeout = timeout
        self._transport = transport

    def _form(self, url, fields):
        if self._transport:
            return self._transport("POST", url, fields)
        return _post_form(url, fields, self.timeout)

    def _json(self, url, token):
        if self._transport:
            return self._transport("GET", url, {"token": token})
        return _get_json(url, token, self.timeout)

    def _store_token(self, payload):
        self.access_token = payload.get("access_token")
        if not self.access_token:
            raise RingError("no access token in the reply")
        self.refresh_token = payload.get("refresh_token", self.refresh_token)
        lifetime = int(payload.get("expires_in", 14400))
        # Renew a minute early, so a request never travels with a token that
        # expires while it is in flight.
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(0, lifetime - 60))
        return self.access_token

    def exchange(self, code):
        return self._store_token(self._form(TOKEN_URL, {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }))

    def refresh(self):
        if not self.refresh_token:
            raise RingError("no refresh token to renew with")
        return self._store_token(self._form(TOKEN_URL, {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }))

    def token(self):
        fresh = self.expires_at is not None and datetime.now(timezone.utc) < self.expires_at
        if self.access_token and fresh:
            return self.access_token
        return self.refresh()

    def devices(self):
        """Every device on the linked account, classified by name."""
        url = "%s/devices?include=status,location" % API_BASE
        payload = self._json(url, self.token())
        records = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            records = [records] if records else []
        found = []
        for record in records:
            try:
                found.append(device_from_ring(record))
            except RingError:
                continue
        return found

    def history_page(self, device_id, since=None, limit=None, url=None):
        """One page of history, and the address of the next one if there is one.

        Pagination is not described in the documentation, so this follows the
        JSON:API convention when it is offered and stops when it is not.
        """
        if url is None:
            query = {}
            if since is not None:
                query["since"] = since.isoformat()
            if limit is not None:
                query["limit"] = str(limit)
            url = "%s/history/devices/%s/events" % (API_BASE, urllib.parse.quote(str(device_id)))
            if query:
                url += "?" + urllib.parse.urlencode(query)

        payload = self._json(url, self.token())
        return normalise_many(payload), next_link(payload)

    def history(self, device_id, since=None, limit=None, max_pages=MAX_HISTORY_PAGES):
        """Every event we are offered for a device, oldest first."""
        events = []
        seen_urls = set()
        url = None
        for _ in range(max_pages):
            page, following = self.history_page(device_id, since=since, limit=limit, url=url)
            events.extend(page)
            if not following or following in seen_urls:
                break
            seen_urls.add(following)
            url = following
        events.sort(key=lambda event: event.at)
        return events

    def subscriptions(self):
        return self._json("%s/accounts/me/subscriptions" % API_BASE, self.token())
