# Contributing

This exists because building on the Ring API without hardware is hard, and the
most useful thing anyone can add is a day the simulator cannot currently
produce.

## Running it

```bash
pip install -e .
python tests/test_simulator.py
```

No dependencies, no build step, no test runner to install. The suite is a plain
script that exits non zero when something is wrong, which is all CI needs.

## What is most wanted

**New scenarios.** A scenario is a household day with a label saying whether
software watching it should raise an alarm. The ones here cover a collapse, an
outing of the same length, a lie in, a caller at an empty house and a camera
that dies. Missing, and worth having:

- someone who gets up in the night and does not go back to bed
- a visitor who stays several hours, so two people move at once
- a house with a second occupant throughout, which no single camera can tell apart
- a camera that reports intermittently rather than failing cleanly
- a routine that shifts over weeks, as a person's does after an illness

Each one is a few lines in `ringsim/scenarios.py`: a hook that changes the
day's plan, and a `Scenario` entry naming the expectation.

**More faithful device behaviour.** Cooldowns, motion rates and coverage are
estimates. Anyone with real Ring data who can correct them would improve
everything built on top.

## What to keep true

**Determinism.** A seed must always produce the same stream. Several of the
checks depend on it, and so does anybody using this to reproduce a bug.

**Event ids derived from the event.** The id is a hash of the timestamp, the
device and the kind, never a counter. A counter restarts at one on every run,
so two overlapping runs produce different events sharing ids, and any consumer
deduplicating on the id silently throws away almost everything it is sent.

**`"source": "simulator"` on every record.** Nothing generated here should ever
be mistakable for a real event further down somebody's pipeline.

**No dependencies.** Standard library only. This is a testing tool, and a
testing tool that drags in a dependency tree is one people work around.

## Style

Follow what is there. Comments explain why, not what. A scenario that needed a
judgement call should say what the call was, because the next person will
wonder.

## Submitting

Open an issue first for anything larger than a scenario, so the shape can be
agreed before you write it. Keep the suite passing. If you add a scenario, add
a check that it behaves as its label promises.
