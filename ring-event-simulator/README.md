# ring-event-simulator

Generates realistic Ring style event streams for a single person living alone,
so that software which reacts to those events can be built and tested without
a house, a camera, or API access.

## Why this exists

Building anything on the Ring API has a chicken and egg problem. You need weeks
of event history to know what a household normally looks like, the sandbox will
not give you weeks of history, and you cannot wait for real time to pass.

Worse, the cases that matter most are the ones you cannot arrange. Nobody is
going to fall over in their kitchen so you can check your alerting works, and
nobody is going to leave a camera unplugged for seven hours to see whether you
mistake a dead camera for a dead person.

This simulator produces those days on demand, labelled with what should happen.

## Install

```bash
pip install -e .
```

Pure standard library, no dependencies.

## Use

```bash
python -m ringsim --list
python -m ringsim --scenario away --days 21 --out events.jsonl --manifest manifest.json
```

Output is JSON Lines, one event per line:

```json
{"event_id": "evt_000412", "device_id": "kitchen", "device_name": "Kitchen", "kind": "motion", "created_at": "2026-09-20T07:41:09.118371+00:00", "source": "simulator"}
```

Every record carries `"source": "simulator"`, so simulated events can never be
mistaken for real ones further down a pipeline.

As a library:

```python
from datetime import date
from ringsim import Simulator
from ringsim.scenarios import SCENARIOS

events, meta = SCENARIOS["fall"].build(Simulator(seed=7), date(2026, 9, 1), 21)
```

## Scenarios

Each one carries an expectation. A scenario marked `no_alert` is a case where a
naive silence timer raises a false alarm.

| Key | Expectation | What it is |
|---|---|---|
| `normal` | no_alert | Ordinary weeks. The history a baseline is learned from. |
| `fall` | alert | Active morning, then every interior device goes quiet, with no door event. |
| `away` | no_alert | The same length of silence as a collapse, opened by a door event. |
| `long_sleep` | no_alert | Silence well past the usual waking hour, at a time of day that tolerates it. |
| `visitor_while_out` | no_alert | A doorbell press mid absence, which is not a return. |
| `camera_offline` | no_alert | A device stops reporting while the person carries on as normal. |

The pairing of `fall` and `away` is the point of the project. At four in the
afternoon both look like seven hours of nothing. The only thing that separates
them is whether a door was opened at the start of the silence.

## How a day is built

Events are not generated directly. What is generated is where the person is,
minute by minute, and the events fall out of that.

1. A routine lays out the day as a sequence of zones: bedroom, bathroom,
   kitchen, living room, with the start time and duration of each drawn around
   a mean so no two days are identical.
2. A trip out replaces a span of the day with a door zone, then `out`, then the
   door zone again. Nothing generates interior events while the zone is `out`,
   which is why an absence needs no special handling.
3. Night bathroom trips are inserted inside the sleep block.
4. Moving between zones borrows the last ninety seconds of a block for the
   hallway.
5. For each block, every device covering that zone samples motion times from a
   Poisson process at its own rate.
6. A device that has just fired stays quiet for its cooldown, the way a real
   camera does.

Seeded throughout, so a given seed always produces the same stream.

## Devices

| Device | Class | Sees |
|---|---|---|
| `front_door` | transit | entry, and doorbell presses |
| `back_door` | transit | back entry |
| `hallway` | interior | hallway |
| `kitchen` | interior | kitchen |
| `living_room` | interior | living room |
| `landing` | interior | bathroom, bedroom |

The transit and interior split is what makes absence detectable. Devices near a
door see someone leaving; devices inside see someone living.

## On the event shape

The record shape follows Ring conventions: snake case keys, an ISO 8601
`created_at`, a `device_id` and an `event_id`. It is a faithful stand in, not a
transcription of a Ring response, and the exact field names should be
reconciled against the API once you have access. Keeping ingestion behind one
adapter makes that a small change.

## Tests

```bash
python tests/test_simulator.py
```

Forty checks covering ordering, determinism, cooldown, and the behaviour each
scenario promises.

## Licence

MIT.
