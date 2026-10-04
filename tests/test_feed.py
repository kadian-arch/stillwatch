"""Checks on the feed, which had none and runs in production every ten minutes.

The feed is the only thing that puts events into the deployed service, and the
one time it broke the dashboard showed a house where nobody had moved for days.
What is proved here is what it posts, by letting it post to a server that
writes down everything it receives.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PASSED = 0
FAILED = []

RECEIVED = []


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


class Catcher(BaseHTTPRequestHandler):
    """A webhook that accepts anything and remembers what it was given."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") or "{}"
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        # Ring wraps a batch in "data", which is what the feed imitates.
        events = payload.get("data") if isinstance(payload, dict) else payload
        RECEIVED.extend(events or [])
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true, "stored": 0}')

    def log_message(self, *args):
        pass


def serve():
    server = HTTPServer(("127.0.0.1", 0), Catcher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d/ring/events" % server.server_address[1]


def feed(*args):
    """Run it the way a person runs it, with a store of its own each time.

    Pointing this at a path in the repository leaves a database behind and,
    worse, lets the second run read the first one's events and take a
    different path through the code while the assertions still pass.
    """
    out = subprocess.run(
        [sys.executable, "feed.py"] + list(args),
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        env=dict(os.environ, STILLWATCH_RING_WEBHOOK_SECRET="test",
                 STILLWATCH_FEED_URL=""),
    )
    return out.returncode, out.stdout + out.stderr


def kinds():
    return {event.get("event_type") or event.get("kind") for event in RECEIVED}


def test_a_still_house_still_has_cameras():
    section("the house goes quiet while the cameras keep reporting")

    server, url = serve()
    try:
        del RECEIVED[:]
        with tempfile.TemporaryDirectory() as folder:
            code, out = feed("--catch-up", "--still", "--url", url,
                             "--db", str(Path(folder) / "events.db"))

        check("it runs", code == 0, out.strip()[-200:])
        check("it says what it is doing",
              "nobody is moving" in out, out.strip()[-200:])
        check("something was actually posted", len(RECEIVED) > 0,
              "%d events" % len(RECEIVED))
        # This is the whole point. An unreachable camera and a person who has
        # stopped moving are different states, and the product's entire claim
        # rests on telling them apart. A demonstration that stops the feed
        # proves the wrong one.
        check("every event is a camera checking in",
              kinds() == {"device_online"}, str(kinds()))
        # Named the way Ring names them on the wire, not the way the engine
        # stores them. An assertion against the stored names passes whatever
        # happens, which is how a useless check hides among working ones.
        check("no movement was invented",
              not any(e.get("event_type") in ("motion_detected", "button_press")
                      for e in RECEIVED),
              str(kinds()))
    finally:
        server.shutdown()


def test_an_ordinary_catch_up_still_moves_her():
    section("an ordinary catch up is unaffected")

    server, url = serve()
    try:
        del RECEIVED[:]
        with tempfile.TemporaryDirectory() as folder:
            code, out = feed("--catch-up", "--url", url, "--seed-days", "2",
                             "--db", str(Path(folder) / "events.db"))

        check("it runs", code == 0, out.strip()[-200:])
        check("it posts movement as well as camera reports",
              "motion_detected" in kinds() and "device_online" in kinds(),
              str(kinds()))
    finally:
        server.shutdown()


def main():
    for test in (test_a_still_house_still_has_cameras,
                 test_an_ordinary_catch_up_still_moves_her):
        test()

    print("\n%d checks passed, %d failed" % (PASSED, len(FAILED)))
    if FAILED:
        for label in FAILED:
            print("  failed: %s" % label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
