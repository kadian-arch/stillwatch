# Stillwatch

**Watches for the silence.**

Every home monitoring product alerts when something happens. For a person living
alone, the dangerous thing is that nothing does. A fall at six in the morning
produces no motion event, no doorbell press and no alert. It produces silence,
and silence is exactly what those products ignore.

Built on the Ring Partner API for the Amazon *Build, Ship, Shape* hackathon.

## The problem nobody solves

A silence timer is easy and useless, because the most common reason a house is
quiet is that nobody is in it. A system that phones a daughter at work every
time her mother goes to the shops gets switched off within a week.

So the real problem is telling these two apart:

- **Absent.** She left. The silence is expected and means nothing.
- **Unresponsive.** She is home and has stopped moving. The silence matters.

Stillwatch classifies each camera as **transit** (at a way in or out) or
**interior** (inside the home), and reads the sequence rather than a threshold.
A stretch of interior silence that opens with a door event is someone leaving.
The same stretch with no door event is someone who stopped.

At four in the afternoon, a collapse and a day out look identical: seven hours
of nothing. The only thing that separates them is whether a door opened at the
start. That distinction is the product.

## What it does

- Learns each household's own rhythm: when they are normally active, and how
  long they are normally still, hour by hour. Half an hour at midday and eight
  hours at midnight, learned rather than configured.
- Judges the present against it, on a ladder from normal to alert, with away
  sitting beside the ladder rather than on it.
- Says why, in a sentence, every single time.
- Tells the family only when it is worth telling them, through Amazon SNS.
- Never looks at anyone. No video analysis, by design.

## Repository

| Folder | What it is |
|---|---|
| [`stillwatch/`](stillwatch/) | The service: learner, monitor, notifications, dashboard, Ring adapter |
| [`ring-event-simulator/`](ring-event-simulator/) | Standalone event simulator, MIT, also published on its own |
| [`docs/`](docs/) | Design, the decisions behind it, deployment, and the developer friction log |
| `check.py` | Runs every test suite |
| `demo_data.py` | Generates the replay days |

## Run it

```bash
pip install -r stillwatch/requirements.txt
python demo_data.py
cd stillwatch
python -m stillwatch serve
```

Open <http://127.0.0.1:8420>, pick a day, and press **Play the day**. Put
*Collapse at home* beside *Out for the day* to see the two judged differently at
the same moment.

Against real Ring events instead:

```bash
python -m stillwatch serve --live --db events.db --person Margarette
```

## Tests

```bash
python check.py
```

Seven suites. The detection rules are tested against days that must alert and
days that must not: a collapse, an outing of the same length, a lie in, a caller
at an empty house, and a camera that dies while the person is fine.

## Ring integration

| Piece | Where |
|---|---|
| OAuth token exchange and refresh | [`stillwatch/ring.py`](stillwatch/stillwatch/ring.py) |
| Event history | same file, `RingClient.history` |
| Webhook receiver with HMAC-SHA256 signature checking | [`stillwatch/service.py`](stillwatch/stillwatch/service.py), `POST /ring/events` |
| Ring event types translated to the engine's own | `ring.py`, `normalise_ring` |

Ring's words are translated in exactly one place, which is why the same engine
runs against the simulator and against real hardware without changing.

## Running it online

Ring requires four HTTPS addresses that the integrator hosts, so Stillwatch has
to be deployed before a real account can be linked.
[docs/DEPLOY.md](docs/DEPLOY.md) covers both ways of getting one: a small
server, where a single script installs Python, Postgres, the service and Caddy,
or a tunnel from a machine you already own.

## Hackathon submission

Built for **Build, Ship, Shape: Amazon Developer Hackathon**.

| | |
|---|---|
| Track | Ring |
| Mini challenge | AWS Builder, through Amazon SNS for caregiver notifications |
| Mini challenge | Open Source, through [ring-event-simulator](ring-event-simulator/) |
| Developer feedback | [docs/FRICTION_LOG.md](docs/FRICTION_LOG.md) |
| How it was built, and why | [docs/DESIGN.md](docs/DESIGN.md), [docs/DECISIONS.md](docs/DECISIONS.md) |

The friction log records the Ring developer experience as it happened, with
severity and suggested fixes for each problem encountered.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
