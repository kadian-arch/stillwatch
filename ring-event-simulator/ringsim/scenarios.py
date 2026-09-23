"""Named situations the detection engine has to get right.

Each scenario carries an expectation. A scenario marked no_alert is a case
where a naive silence timer would raise a false alarm, and raising one is a
test failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from .engine import ENTRY, Block, DOOR_SECONDS, Outage, Simulator, clip

ALERT = "alert"
NO_ALERT = "no_alert"


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    expectation: str
    description: str
    build: Callable


def _target(start_day, days):
    return start_day + timedelta(days=days - 1)


def _meta(scenario_key, title, expectation, target_day, **extra):
    record = {
        "scenario": scenario_key,
        "title": title,
        "expectation": expectation,
        "target_day": target_day.isoformat(),
    }
    record.update(extra)
    return record


def build_normal(sim: Simulator, start_day, days):
    events = sim.generate(start_day, days)
    return events, _meta("normal", "Ordinary weeks", NO_ALERT, _target(start_day, days))


def build_fall(sim: Simulator, start_day, days, at_minute=555):
    target = _target(start_day, days)

    def hook(simulator, plan):
        if plan.day != target:
            return plan
        cut = simulator.midnight(plan.day) + timedelta(minutes=at_minute)
        # Sleep blocks starting on the target day run past midnight, and the
        # night trips inside them do too, so the cut has to outlive the day.
        plan.blocks = clip(plan.blocks, cut, cut + timedelta(days=3))
        plan.dings = [when for when in plan.dings if when < cut]
        return plan

    events = sim.generate(start_day, days, day_hook=hook)
    return events, _meta(
        "fall",
        "Collapse at home",
        ALERT,
        target,
        stops_at=(sim.midnight(target) + timedelta(minutes=at_minute)).isoformat(),
    )


def build_away(sim: Simulator, start_day, days, depart_minute=490, hours=9.0):
    target = _target(start_day, days)

    def hook(simulator, plan):
        if plan.day != target:
            return plan
        depart = simulator.midnight(plan.day) + timedelta(minutes=depart_minute)
        plan.blocks = simulator.absence(plan.blocks, depart, depart + timedelta(hours=hours))
        return plan

    events = sim.generate(start_day, days, day_hook=hook)
    return events, _meta(
        "away",
        "Out for the day",
        NO_ALERT,
        target,
        departs_at=(sim.midnight(target) + timedelta(minutes=depart_minute)).isoformat(),
    )


def build_long_sleep(sim: Simulator, start_day, days, until_minute=580):
    target = _target(start_day, days)

    def hook(simulator, plan):
        if plan.day != target:
            return plan
        midnight = simulator.midnight(plan.day)
        plan.blocks = clip(plan.blocks, midnight, midnight + timedelta(minutes=until_minute))
        return plan

    events = sim.generate(start_day, days, day_hook=hook)
    return events, _meta(
        "long_sleep",
        "A long lie in",
        NO_ALERT,
        target,
        wakes_at=(sim.midnight(target) + timedelta(minutes=until_minute)).isoformat(),
    )


def build_visitor_while_out(sim: Simulator, start_day, days, depart_minute=490, hours=8.0):
    target = _target(start_day, days)

    def hook(simulator, plan):
        if plan.day != target:
            return plan
        depart = simulator.midnight(plan.day) + timedelta(minutes=depart_minute)
        back = depart + timedelta(hours=hours)
        plan.blocks = simulator.absence(plan.blocks, depart, back)
        caller = depart + timedelta(hours=hours / 2)
        plan.blocks.append(Block(caller, caller + timedelta(seconds=DOOR_SECONDS), ENTRY))
        plan.blocks.sort(key=lambda block: block.start)
        plan.dings.append(caller + timedelta(seconds=20))
        return plan

    events = sim.generate(start_day, days, day_hook=hook)
    return events, _meta(
        "visitor_while_out",
        "Caller at an empty house",
        NO_ALERT,
        target,
        note="A transit event during an absence is not a return.",
    )


def build_camera_offline(sim: Simulator, start_day, days, device_id="kitchen", from_minute=420, hours=7.0):
    target = _target(start_day, days)
    start = sim.midnight(target) + timedelta(minutes=from_minute)
    outage = Outage(device_id, start, start + timedelta(hours=hours))
    events = sim.generate(start_day, days, outages=(outage,))
    return events, _meta(
        "camera_offline",
        "One camera drops out",
        NO_ALERT,
        target,
        offline_device=device_id,
        offline_from=outage.start.isoformat(),
        offline_until=outage.end.isoformat(),
        note="Device silence is a fault, not an absence of the person.",
    )


SCENARIOS = {
    "normal": Scenario(
        "normal",
        "Ordinary weeks",
        NO_ALERT,
        "Baseline behaviour. Supplies the history the rhythm learner trains on.",
        build_normal,
    ),
    "fall": Scenario(
        "fall",
        "Collapse at home",
        ALERT,
        "Active morning, then every interior device goes quiet with no door event.",
        build_fall,
    ),
    "away": Scenario(
        "away",
        "Out for the day",
        NO_ALERT,
        "The same length of interior silence as a collapse, opened by a door event.",
        build_away,
    ),
    "long_sleep": Scenario(
        "long_sleep",
        "A long lie in",
        NO_ALERT,
        "Silence well past the usual waking hour, at a time of day that tolerates it.",
        build_long_sleep,
    ),
    "visitor_while_out": Scenario(
        "visitor_while_out",
        "Caller at an empty house",
        NO_ALERT,
        "A doorbell press mid absence must not be mistaken for a return.",
        build_visitor_while_out,
    ),
    "camera_offline": Scenario(
        "camera_offline",
        "One camera drops out",
        NO_ALERT,
        "A device stops reporting while the person carries on as normal.",
        build_camera_offline,
    ),
}
