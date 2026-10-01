"""Writes the recorded days the dashboard offers beside a real household.

A home that was connected this morning has nothing to show, and a blank page
is a poor way to explain what a product does. These are six days of a
simulated household, each one built to end somewhere worth looking at: a fall,
a long trip out, a lie in, a caller at an empty house, a camera that drops out,
and a day where nothing happens at all.

They are written rather than committed because a hosted container starts with
an empty disk on every restart, and because a few megabytes of generated
events do not belong in a repository that can rebuild them in a second.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

START = date(2026, 8, 24)
DAYS = 28
SEED = 7
PERSON = "Margarette"

ORDER = ("fall", "away", "long_sleep", "visitor_while_out", "camera_offline", "normal")


def _simulator_on_path(root=None):
    """The simulator is a sibling package in this repository, not a dependency."""
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    folder = root / "ring-event-simulator"
    if folder.is_dir() and str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


def already_built(folder):
    folder = Path(folder)
    return (folder / "scenarios.json").exists() and any(folder.glob("*.jsonl"))


def build(folder, start=START, days=DAYS, seed=SEED, on_each=None, repo_root=None):
    """Write every recorded day into `folder`. Returns what was written."""
    _simulator_on_path(repo_root)

    from ringsim import Simulator
    from ringsim.devices import DEFAULT_DEVICES, manifest
    from ringsim.events import write_jsonl
    from ringsim.scenarios import SCENARIOS

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    index = []
    for key in ORDER:
        scenario = SCENARIOS[key]
        events, meta = scenario.build(Simulator(seed=seed), start, days)
        with open(folder / ("%s.jsonl" % key), "w", encoding="utf-8") as handle:
            count = write_jsonl(events, handle)
        index.append({
            "key": key,
            "title": scenario.title,
            "description": scenario.description,
            "expectation": scenario.expectation,
            "target_day": meta["target_day"],
        })
        if on_each is not None:
            on_each(key, count, meta["target_day"])

    with open(folder / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump({"devices": manifest(DEFAULT_DEVICES)}, handle, indent=2)
    with open(folder / "scenarios.json", "w", encoding="utf-8") as handle:
        json.dump({"persona": PERSON, "seed": seed, "scenarios": index}, handle, indent=2)
    return index


def ensure(folder, repo_root=None):
    """Build them if they are not there. Safe to call on every start."""
    if already_built(folder):
        return False
    build(folder, repo_root=repo_root)
    return True
