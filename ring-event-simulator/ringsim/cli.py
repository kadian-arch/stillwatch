"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta

from .devices import DEFAULT_DEVICES, manifest
from .events import write_jsonl
from .engine import Simulator
from .persona import PERSONAS
from .scenarios import SCENARIOS


def _parse_date(text):
    return datetime.strptime(text, "%Y-%m-%d").date()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ringsim",
        description="Generate Ring style event streams for a person living alone.",
    )
    parser.add_argument("--scenario", default="normal", choices=sorted(SCENARIOS))
    parser.add_argument("--persona", default="margarette", choices=sorted(PERSONAS))
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--start", type=_parse_date, default=None,
                        help="First day of the stream. Defaults so the stream ends today.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default="-", help="JSON Lines output path, or - for stdout.")
    parser.add_argument("--manifest", default=None, help="Optional path for the device and scenario manifest.")
    parser.add_argument("--list", action="store_true", help="List scenarios and exit.")
    parser.add_argument("--timeline", action="store_true",
                        help="Print an hour by hour grid of the final day.")
    return parser


def timeline(events, day, devices=DEFAULT_DEVICES):
    """One row per device, one column per hour, for a single day."""
    grid = {device.device_id: [0] * 24 for device in devices}
    for event in events:
        if event.created_at.date() == day and event.device_id in grid:
            grid[event.device_id][event.created_at.hour] += 1

    lines = ["", "%s   %s" % (day.isoformat(), "".join(str(h % 10) for h in range(24)))]
    for device in devices:
        cells = "".join(
            "." if count == 0 else ("#" if count > 9 else str(count))
            for count in grid[device.device_id]
        )
        lines.append("%-12s %-3s %s" % (device.device_id, device.zone_class[:3], cells))
    return "\n".join(lines)


def describe(events, meta):
    kinds = Counter(event.kind for event in events)
    devices = Counter(event.device_id for event in events)
    lines = [
        "scenario   %s (%s)" % (meta["scenario"], meta["expectation"]),
        "events     %d" % len(events),
        "span       %s to %s" % (events[0].created_at.isoformat(), events[-1].created_at.isoformat()),
        "kinds      %s" % ", ".join("%s=%d" % pair for pair in sorted(kinds.items())),
        "devices    %s" % ", ".join("%s=%d" % pair for pair in sorted(devices.items())),
    ]
    return "\n".join(lines)


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.list:
        for scenario in SCENARIOS.values():
            print("%-18s %-10s %s" % (scenario.key, scenario.expectation, scenario.description))
        return 0

    if args.days < 1:
        print("days must be at least 1", file=sys.stderr)
        return 2

    start_day = args.start or (date.today() - timedelta(days=args.days - 1))
    simulator = Simulator(persona=PERSONAS[args.persona], seed=args.seed)
    events, meta = SCENARIOS[args.scenario].build(simulator, start_day, args.days)

    if not events:
        print("no events generated", file=sys.stderr)
        return 1

    if args.out == "-":
        write_jsonl(events, sys.stdout)
    else:
        with open(args.out, "w", encoding="utf-8") as handle:
            write_jsonl(events, handle)

    if args.manifest:
        payload = {
            "devices": manifest(DEFAULT_DEVICES),
            "scenario": meta,
            "persona": args.persona,
            "seed": args.seed,
            "days": args.days,
            "start_day": start_day.isoformat(),
        }
        with open(args.manifest, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    print(describe(events, meta), file=sys.stderr)
    if args.timeline:
        print(timeline(events, start_day + timedelta(days=args.days - 1)), file=sys.stderr)
    return 0
