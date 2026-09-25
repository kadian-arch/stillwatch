"""Generate every replay scenario for the dashboard, with an index describing them."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "ring-event-simulator"))

from ringsim import Simulator
from ringsim.devices import DEFAULT_DEVICES, manifest
from ringsim.events import write_jsonl
from ringsim.scenarios import SCENARIOS

DATA = ROOT / "stillwatch" / "data"
START = date(2026, 8, 24)
DAYS = 28
SEED = 7

ORDER = ("fall", "away", "long_sleep", "visitor_while_out", "camera_offline", "normal")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    index = []
    for key in ORDER:
        scenario = SCENARIOS[key]
        events, meta = scenario.build(Simulator(seed=SEED), START, DAYS)
        with open(DATA / ("%s.jsonl" % key), "w", encoding="utf-8") as handle:
            count = write_jsonl(events, handle)
        index.append({
            "key": key,
            "title": scenario.title,
            "description": scenario.description,
            "expectation": scenario.expectation,
            "target_day": meta["target_day"],
        })
        print("  %-18s %5d events, watch %s" % (key, count, meta["target_day"]))

    with open(DATA / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump({"devices": manifest(DEFAULT_DEVICES)}, handle, indent=2)
    with open(DATA / "scenarios.json", "w", encoding="utf-8") as handle:
        json.dump({"persona": "Margarette", "seed": SEED, "scenarios": index}, handle, indent=2)

    print("\nwrote %d scenarios to %s" % (len(index), DATA))
    return 0


if __name__ == "__main__":
    sys.exit(main())
