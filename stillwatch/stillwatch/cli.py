"""Command line: learn a baseline, or watch a day unfold against one."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, time, timedelta, timezone

from dataclasses import replace

from .model import last_covered_day, load_events, load_roster
from .monitor import ALERT, AWAY, CONCERN, NORMAL, QUIET, UNKNOWN, changes, peak, walk
from .rhythm import ANY_INTERIOR, DAYTYPES, Baseline, human_duration, learn

READABLE_HOURS = (3, 8, 11, 14, 17, 20, 23)

MARKS = {NORMAL: "N", QUIET: "Q", CONCERN: "C", ALERT: "A", AWAY: "W", UNKNOWN: "?"}


def _cell(rate):
    if rate >= 0.95:
        return "#"
    if rate < 0.05:
        return "."
    return str(min(9, int(rate * 10)))


def activity_report(baseline, roster, daytype):
    days = baseline.days_observed.get(daytype, 0)
    lines = ["", "%s rhythm, %d days observed" % (daytype, days),
             "  share of days with activity in that hour: . under 5%, 0 to 9 by tenths, # at least 95%"]
    lines.append("%-14s %s" % ("", "".join(str(hour % 10) for hour in range(24))))
    for device in roster:
        row = "".join(_cell(baseline.activity_rate(device.device_id, daytype, hour))
                      for hour in range(24))
        lines.append("%-14s %s" % (device.device_id, row))
    return "\n".join(lines)


def quiet_report(baseline, daytype):
    lines = ["", "tolerated quiet on a %s, 95th percentile" % daytype,
             "  longest silence she normally produces, by the hour the silence begins",
             "  whole hours, . means no baseline, + means ten or more"]
    lines.append("%-14s %s" % ("", "".join(str(hour % 10) for hour in range(24))))
    row = ""
    for hour in range(24):
        seconds = baseline.tolerated_quiet(daytype, hour)
        if seconds is None:
            row += "."
            continue
        hours = seconds / 3600.0
        row += "+" if hours >= 10 else str(int(hours))
    lines.append("%-14s %s" % ("hours", row))
    for hour in READABLE_HOURS:
        lines.append("  " + baseline.describe_quiet(daytype, hour))
    return "\n".join(lines)


def anchor_report(baseline, roster):
    lines = ["", "anchors, activity that happens on most days at about the same time"]
    if not baseline.anchors:
        lines.append("  none found")
        return "\n".join(lines)
    ordered = sorted(baseline.anchors, key=lambda a: (a.daytype, a.median_minute))
    lines.append("  %-9s %-14s %-8s %-6s %-8s %s"
                 % ("daytype", "device", "window", "usual", "spread", "days seen"))
    for anchor in ordered:
        label = "any interior" if anchor.device_id == ANY_INTERIOR else roster.name(anchor.device_id)
        lines.append("  %-9s %-14s %-8s %-6s %-8s %d%% of %d"
                     % (anchor.daytype, label, anchor.window, anchor.clock(),
                        "%.0f min" % anchor.spread_minutes,
                        round(anchor.hit_rate * 100), anchor.observed_days))
    return "\n".join(lines)


def cmd_learn(args):
    events = load_events(args.events)
    roster = load_roster(args.manifest)
    if not events:
        print("no events in %s" % args.events, file=sys.stderr)
        return 1

    baseline = learn(events, roster)
    if args.out:
        baseline.save(args.out)
    if args.quiet:
        return 0

    total = sum(baseline.days_observed.values())
    print("learned from %d full days, %s to %s"
          % (total, baseline.first_event[:16], baseline.last_event[:16]))
    print("  interior devices: %s" % ", ".join(roster.interior_ids()))
    print("  transit devices:  %s" % ", ".join(roster.transit_ids()))
    print("  %d silences were opened by a door event and left out of the baseline"
          % baseline.excluded_absences)
    print(anchor_report(baseline, roster))
    for daytype in DAYTYPES:
        print(activity_report(baseline, roster, daytype))
    for daytype in DAYTYPES:
        print(quiet_report(baseline, daytype))
    return 0


def cmd_watch(args):
    events = load_events(args.events)
    roster = load_roster(args.manifest)
    if not events:
        print("no events in %s" % args.events, file=sys.stderr)
        return 1

    day = (datetime.strptime(args.day, "%Y-%m-%d").date() if args.day
           else last_covered_day(events))
    midnight = datetime.combine(day, time(0, 0), tzinfo=timezone.utc)

    if args.baseline:
        baseline = Baseline.load(args.baseline)
    else:
        baseline = learn(events, roster, until=midnight)

    observed = sum(baseline.days_observed.values())
    print("watching %s, judged against %d days of history" % (day.isoformat(), observed))

    start = midnight + timedelta(hours=args.start_hour)
    # Deliberately not capped at the last event. A collapse is the absence of
    # events, so the hours after the stream falls silent are the point.
    end = midnight + timedelta(hours=args.end_hour)
    readings = walk(events, baseline, roster, start, end, args.step)
    if not readings:
        print("nothing to watch on %s" % day.isoformat(), file=sys.stderr)
        return 1

    strip = "".join(MARKS.get(reading.state, "?") for reading in readings)
    print("\n%s to %s in %d minute steps"
          % (start.strftime("%H:%M"), end.strftime("%H:%M"), args.step))
    print("  %s" % strip)
    print("  N normal, Q quiet, C concern, A alert, W away, ? cannot tell")

    print("\nstate changes")
    for reading in changes(readings):
        print("  %s  %-8s %s" % (reading.at.strftime("%H:%M"), reading.state, reading.headline))
        if args.why:
            for line in reading.reasons:
                print("        %s" % line)

    worst = peak(readings)
    print("\nhighest rung reached: %s" % (worst.state if worst else "none"))
    if worst and worst.needs_attention:
        print("\nwhat a caregiver would be told")
        print("  %s" % worst.headline)
        for line in worst.reasons:
            print("    %s" % line)
    return 0


def cmd_ingest(args):
    """Load an event file into the store, the way a history backfill will."""
    from .store import EventStore

    events = load_events(args.events)
    store = EventStore(args.db)
    if args.manifest:
        store.remember_devices(list(load_roster(args.manifest)))

    shift = timedelta(0)
    if args.end_now and events:
        shift = datetime.now(timezone.utc) - events[-1].at
        events = [replace(event, at=event.at + shift) for event in events]

    stored = store.add_many(events)
    print("read %d events, stored %d new ones in %s" % (len(events), stored, args.db))
    if shift:
        print("  shifted forward by %s so the history ends now" % human_duration(shift.total_seconds()))
    print("  the store now holds %d events across %d devices"
          % (store.count(), len(store.roster())))
    store.close()
    return 0


def cmd_backfill(args):
    from .backfill import backfill, client_from_store
    from .ring import RingError
    from .store import EventStore

    store = EventStore(args.db)
    try:
        client = client_from_store(store)
    except RingError as error:
        print("cannot reach Ring: %s" % error, file=sys.stderr)
        print("set STILLWATCH_RING_CLIENT_ID and STILLWATCH_RING_CLIENT_SECRET,"
              " and link the home first", file=sys.stderr)
        return 1

    print("reading history from Ring, %d days back" % args.days)
    report = backfill(
        client, store, days=args.days,
        on_progress=lambda device, read, new: print(
            "  %-16s %4d read, %4d new" % (device.name, read, new)),
    )
    print()
    print(report.summary())
    print()
    print("the store now holds %d events across %d devices"
          % (store.count(), len(store.roster())))
    store.close()
    return 0 if report.ok else 1


def cmd_notify_test(args):
    """Send one message through whatever channels the environment names."""
    from datetime import datetime, timezone

    from .notify import Notice, channels_from_env

    channels = channels_from_env()
    reaching = [c for c in channels if getattr(c, "reaches_people", True)]
    if not reaching:
        print("no channel that reaches anyone is configured. Set either", file=sys.stderr)
        print("  STILLWATCH_SMTP_HOST and STILLWATCH_EMAIL_TO, or", file=sys.stderr)
        print("  STILLWATCH_SNS_TOPIC_ARN", file=sys.stderr)
        return 2

    notice = Notice(
        at=datetime.now(timezone.utc),
        kind="alert",
        urgency="urgent",
        subject="Stillwatch: test message, no action needed",
        body=("This is a test of the alert path for %s.\n\n"
              "- If this reached you, a real alert would too.\n"
              "- Nothing is wrong. Nobody needs checking on." % args.person),
        state="ALERT",
    )

    failed = 0
    for channel in channels:
        try:
            channel.send(notice)
        except Exception as error:
            print("  %-8s FAILED  %s: %s" % (channel.name, type(error).__name__, error))
            failed += 1
            continue
        print("  %-8s sent" % channel.name)
    return 1 if failed else 0


def cmd_probe(args):
    """Call the real Ring API with a Playground token and report what comes back.

    The Playground issues a short lived token against synthetic devices, which
    is the only way to exercise this client against Ring itself without owning
    hardware. Whatever it prints is Ring's own answer, not ours.
    """
    from datetime import datetime, timedelta, timezone

    from .ring import API_BASE, RingClient, RingError

    token = args.token.strip()
    if not token:
        print("paste the token from the Ring Playground with --token", file=sys.stderr)
        return 2

    client = RingClient(
        client_id=os.environ.get("STILLWATCH_RING_CLIENT_ID", "playground"),
        client_secret=os.environ.get("STILLWATCH_RING_CLIENT_SECRET", ""),
        access_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=25),
    )

    print("asking %s" % API_BASE)
    try:
        devices = client.devices()
    except RingError as error:
        print("  devices: %s" % error, file=sys.stderr)
        return 1
    except Exception as error:
        print("  devices: %s: %s" % (type(error).__name__, error), file=sys.stderr)
        return 1

    print("\ndevices: %d" % len(devices))
    for device in devices:
        print("  %-28s %-9s %s" % (device.name, device.zone_class, device.device_id))

    if not devices:
        print("  the account this token belongs to reports no devices")
        return 0

    if args.raw:
        import json as _json

        raw = client.fetch("/v1/devices?include=status,location")
        print()
        print(_json.dumps(raw, indent=2)[:4000])
        for record in (raw.get("data") or []):
            links = record.get("relationships") or {}
            for name in ("capabilities", "status"):
                related = ((links.get(name) or {}).get("links") or {}).get("related")
                if not related:
                    continue
                print()
                print("--- %s ---" % name)
                try:
                    print(_json.dumps(client.fetch(related), indent=2)[:2500])
                except Exception as error:
                    print("  %s: %s" % (type(error).__name__, error))
        return 0

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    print("\nhistory since %s" % since.date().isoformat())
    for device in devices:
        try:
            events = client.history(device.device_id, since=since)
        except RingError as error:
            print("  %-28s refused: %s" % (device.name, error))
            continue
        except Exception as error:
            print("  %-28s %s: %s" % (device.name, type(error).__name__, error))
            continue
        print("  %-28s %d events" % (device.name, len(events)))
        if events:
            first = events[0]
            print("      earliest %s  %s" % (first.at.isoformat(), first.kind))
    return 0


def cmd_serve(args):
    try:
        from .service import CompositeSource, LiveSource, ReplaySource, create_app
    except ImportError:
        print("the dashboard needs Flask: pip install -r requirements.txt", file=sys.stderr)
        return 1

    store = None
    secret = os.environ.get("STILLWATCH_RING_WEBHOOK_SECRET", "").strip()

    if args.live:
        from .store import EventStore

        store = EventStore(args.db)
        source = LiveSource(store, person=args.person)
        demo = ReplaySource(args.data) if args.demo else None
        if demo is not None and demo.scenarios():
            source = CompositeSource(source, demo)
        print("Stillwatch on http://%s:%d, live" % (args.host, args.port))
        print("  events in %s: %d" % (args.db, store.count()))
        if demo is not None and demo.scenarios():
            print("  %d recorded days offered alongside, marked as demonstrations"
                  % len(demo.scenarios()))
        if secret:
            print("  Ring webhook ready at POST /ring/events")
        else:
            print("  no STILLWATCH_RING_WEBHOOK_SECRET set, so /ring/events will refuse traffic")
    else:
        source = ReplaySource(args.data)
        if not source.scenarios():
            print("no replay data in %s. From the project folder run: python demo_data.py"
                  % args.data, file=sys.stderr)
            return 1
        print("Stillwatch on http://%s:%d, replay" % (args.host, args.port))
        print("  %d scenarios from %s" % (len(source.scenarios()), args.data))

    watcher = None
    if args.live and args.notify:
        from .notify import channels_from_env
        from .watch import LiveWatcher

        channels = channels_from_env()
        watcher = LiveWatcher(store, args.person, channels).start()
        print("  watching for silence, telling: %s" % ", ".join(watcher.channels))

    create_app(source, store=store, webhook_secret=secret).run(
        host=args.host, port=args.port, debug=False)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="stillwatch",
        description="Learn a household baseline, and watch a day against it.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    learner = commands.add_parser("learn", help="Learn a baseline from an event stream.")
    learner.add_argument("--events", required=True)
    learner.add_argument("--manifest", required=True)
    learner.add_argument("--out", default=None)
    learner.add_argument("--quiet", action="store_true", help="Write the baseline, print nothing.")
    learner.set_defaults(handler=cmd_learn)

    watcher = commands.add_parser("watch", help="Walk a single day against a baseline.")
    watcher.add_argument("--events", required=True)
    watcher.add_argument("--manifest", required=True)
    watcher.add_argument("--baseline", default=None,
                         help="Use a saved baseline instead of learning from history before the day.")
    watcher.add_argument("--day", default=None, help="YYYY-MM-DD. Defaults to the last day present.")
    watcher.add_argument("--step", type=int, default=15, help="Minutes between assessments.")
    watcher.add_argument("--start-hour", type=int, default=0, dest="start_hour")
    watcher.add_argument("--end-hour", type=int, default=23, dest="end_hour")
    watcher.add_argument("--why", action="store_true", help="Print the reasoning for every change.")
    watcher.set_defaults(handler=cmd_watch)

    loader = commands.add_parser("ingest", help="Load an event file into the store.")
    loader.add_argument("--events", required=True)
    loader.add_argument("--manifest", default=None)
    loader.add_argument("--db", default="events.db")
    loader.add_argument("--end-now", action="store_true", dest="end_now",
                        help="Shift the history so it ends at the present moment.")
    loader.set_defaults(handler=cmd_ingest)

    filler = commands.add_parser("backfill", help="Read past events from Ring into the store.")
    filler.add_argument("--db", default="events.db")
    filler.add_argument("--days", type=int, default=30,
                        help="How far back to ask for. Defaults to 30 days.")
    filler.set_defaults(handler=cmd_backfill)

    tester = commands.add_parser(
        "notify-test", help="Send one test message through the configured channels.")
    tester.add_argument("--person", default="this household")
    tester.set_defaults(handler=cmd_notify_test)

    prober = commands.add_parser(
        "probe", help="Call the real Ring API with a Playground token.")
    prober.add_argument("--token", default="", help="A token from the Ring Playground.")
    prober.add_argument("--days", type=int, default=7, help="How far back to ask for.")
    prober.add_argument("--raw", action="store_true",
                        help="Print what Ring actually sends, including capabilities.")
    prober.set_defaults(handler=cmd_probe)

    server = commands.add_parser("serve", help="Run the dashboard.")
    server.add_argument("--data", default="data", help="Folder holding the replay files.")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8420)
    server.add_argument("--notify", action="store_true",
                        help="Judge the house on a timer and send real messages.")
    server.add_argument("--demo", action="store_true",
                        help="Also offer the recorded days, marked as demonstrations.")
    server.add_argument("--live", action="store_true",
                        help="Serve real events from the store instead of replays.")
    server.add_argument("--db", default="events.db", help="Event store for live mode.")
    server.add_argument("--person", default="The household",
                        help="Who is being watched over, for the wording of messages.")
    server.set_defaults(handler=cmd_serve)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.handler(args)
