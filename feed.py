"""Feeds Ring shaped events into a running Stillwatch over its own webhook.

Ring cameras are not sold in the country this was built in, so there was no
hardware to take events from. Rather than replay a recorded file into the
dashboard, this posts events to `/ring/events` exactly as Ring would: the same
payload shape, signed with the same HMAC key, arriving one at a time as the
clock reaches them.

Everything past that endpoint is the real path. The signature is checked, the
payload is translated by the Ring adapter, written to the store idempotently,
and judged by the same engine that would judge a real home. Notifications go
out through the same notifier. The only invented part is where the motion came
from, and the dashboard says so.

Devices are registered straight into the store, because Ring normally supplies
those from its devices endpoint, which needs an account this has no way to
stand in for.

    python feed.py --seed-days 21          fill in a past, then run forward
    python feed.py --no-seed               just run forward from now
    python feed.py --speed 60              an hour of household time a minute

Stop it with Ctrl+C. Run it again and nothing is duplicated, because every
event carries a stable id.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "ring-event-simulator"))
sys.path.insert(0, os.path.join(ROOT, "stillwatch"))

from ringsim.devices import DEFAULT_DEVICES, manifest
from ringsim.engine import Simulator
from ringsim.persona import MARGARETTE

from stillwatch.ring import sign

# Ring's own words, so the adapter is exercised rather than bypassed.
WIRE_KIND = {
    "motion": "motion_detected",
    "ding": "button_press",
    "offline": "device_offline",
    "online": "device_online",
}

CHUNK = 400
TICK_SECONDS = 20

# A webhook is not a file. Ring redelivers anything it is not certain arrived,
# deliveries overtake each other, and a camera on a weak link misses some
# motion altogether. Feeding clean, ordered, exactly-once events would test a
# path that does not exist in production.
DUPLICATE_RATE = 0.02
DROP_RATE = 0.004


def as_ring(event):
    """One simulator event in the shape Ring puts on the wire."""
    return {
        "id": event.event_id,
        "device_id": event.device_id,
        "event_type": WIRE_KIND.get(event.kind, event.kind),
        "created_at": event.created_at.astimezone(timezone.utc).isoformat(),
    }


def wire(events, rng, duplicate_rate, drop_rate):
    """Turn events into what actually comes down a webhook."""
    records = []
    for event in events:
        if rng.random() < drop_rate:
            continue
        records.append(as_ring(event))
        if rng.random() < duplicate_rate:
            records.append(as_ring(event))
    return records


def post(url, secret, records, timeout=20):
    """Deliver a batch the way Ring delivers one, signature and all."""
    body = json.dumps({"data": records}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": sign(secret, body),
            "User-Agent": "stillwatch-feed",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8") or "{}")


def send(url, secret, events, label, rng=None,
         duplicate_rate=DUPLICATE_RATE, drop_rate=DROP_RATE):
    """Post events in batches, reporting what the service made of them.

    Sent and new differ by the redeliveries, which is the store refusing to
    count the same motion twice.
    """
    rng = rng or random.Random(0)
    records = wire(events, rng, duplicate_rate, drop_rate)
    stored = 0
    for start in range(0, len(records), CHUNK):
        batch = records[start:start + CHUNK]
        # Deliveries overtake each other on the way, within a batch at least.
        rng.shuffle(batch)
        try:
            answer = post(url, secret, batch)
        except urllib.error.HTTPError as error:
            if error.code == 401:
                sys.exit("the webhook rejected the signature. The secret here does "
                         "not match the one the dashboard was started with.")
            sys.exit("the webhook answered %s: %s" % (error.code, error.reason))
        except urllib.error.URLError as error:
            sys.exit("could not reach %s: %s" % (url, error.reason))
        stored += answer.get("stored", 0)
    if records:
        print("  %-24s %5d sent, %5d new" % (label, len(records), stored))
    return stored


def register(db):
    """Ring supplies the device list from its own endpoint. This stands in."""
    from stillwatch.model import Device
    from stillwatch.store import EventStore

    store = EventStore(db)
    store.remember_devices(
        Device(row["device_id"], row["name"], row["zone_class"]) for row in manifest()
    )
    return len(manifest())


def day_events(sim, when):
    return sim.generate(when, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("STILLWATCH_FEED_URL",
                                                        "http://127.0.0.1:8420/ring/events"))
    parser.add_argument("--secret", default=os.environ.get("STILLWATCH_RING_WEBHOOK_SECRET", ""))
    parser.add_argument("--db", default=os.environ.get("STILLWATCH_DB", "stillwatch/events.db"),
                        help="Where the dashboard keeps its events, for the device list.")
    parser.add_argument("--seed-days", type=int, default=60,
                        help="Days of past to fill in first, so a baseline exists.")
    parser.add_argument("--no-seed", action="store_true")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Household seconds per real second. 1 is real time.")
    parser.add_argument("--seed", type=int, default=7, help="Simulator seed.")
    parser.add_argument("--clean", action="store_true",
                        help="Deliver exactly once and in order, which no real webhook does.")
    args = parser.parse_args()

    secret = args.secret.strip()
    if not secret:
        sys.exit("no webhook secret. Set STILLWATCH_RING_WEBHOOK_SECRET, the same "
                 "value the dashboard was started with.")

    sim = Simulator(persona=MARGARETTE, devices=DEFAULT_DEVICES, seed=args.seed)
    rng = random.Random(args.seed + 1)
    dup = 0.0 if args.clean else DUPLICATE_RATE
    drop = 0.0 if args.clean else DROP_RATE

    print("feeding %s" % args.url)
    print("  devices registered: %d" % register(args.db))

    today = datetime.now(timezone.utc).date()

    if not args.no_seed and args.seed_days > 0:
        print("\nfilling in %d days of history" % args.seed_days)
        first = today - timedelta(days=args.seed_days)
        past = [e for e in sim.generate(first, args.seed_days)
                if e.created_at.date() < today]
        send(args.url, secret, past, "%s to yesterday" % first.isoformat(),
             rng, dup, drop)

    print("\ntoday so far")
    now = datetime.now(timezone.utc)
    plan = day_events(sim, today)
    already = [e for e in plan if e.created_at <= now]
    send(args.url, secret, already, "up to %s" % now.strftime("%H:%M"),
         rng, dup, drop)

    pending = [e for e in plan if e.created_at > now]
    print("\nrunning forward, %d events left today%s" % (
        len(pending), "" if args.speed == 1 else " at %gx" % args.speed))
    print("Ctrl+C to stop.\n")

    # Household time can run faster than the clock, which is the only way to
    # watch a whole day inside a demonstration. Offset grows as we outrun it.
    started = datetime.now(timezone.utc)
    offset = timedelta(0)

    try:
        while True:
            time.sleep(TICK_SECONDS)
            real_now = datetime.now(timezone.utc)
            if args.speed != 1.0:
                elapsed = real_now - started
                offset = elapsed * (args.speed - 1.0)
            household_now = real_now + offset

            due = [e for e in pending if e.created_at <= household_now]
            if due:
                pending = [e for e in pending if e.created_at > household_now]
                send(args.url, secret, due, household_now.strftime("%H:%M"),
                     rng, dup, drop)

            if not pending:
                next_day = household_now.date() + timedelta(days=1)
                print("\n%s" % next_day.isoformat())
                plan = day_events(sim, next_day)
                pending = [e for e in plan if e.created_at > household_now]
    except KeyboardInterrupt:
        print("\nstopped. %d events were still to come today." % len(pending))
        return 0


if __name__ == "__main__":
    sys.exit(main())
