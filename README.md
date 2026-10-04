# Stillwatch

[![tests](https://github.com/kadian-arch/stillwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/kadian-arch/stillwatch/actions/workflows/ci.yml)

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
| [`ring-event-simulator/`](ring-event-simulator/) | Standalone event simulator, MIT, published separately at [kadian-arch/ring-event-simulator](https://github.com/kadian-arch/ring-event-simulator) |
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

Open <http://127.0.0.1:8420>, pick a day, and press **Play**. Put *Collapse at
home* beside *Out for the day* to see the two judged differently at the same
moment.

### Seeing it raise an alarm

A recorded day replays an alarm but cannot be answered, because answering is
something you do about today. For a live household that is genuinely in
trouble, build forty days ending in a collapse and land that collapse
seventeen hours in the past:

```bash
cd ring-event-simulator
python -m ringsim --scenario fall --days 40 --out fall.jsonl --manifest manifest.json
cd ../stillwatch
python -m stillwatch ingest --db alarm.db --ends-ago 17 \
  --events ../ring-event-simulator/fall.jsonl \
  --manifest ../ring-event-simulator/manifest.json
python -m stillwatch serve --live --db alarm.db --person Margarette
```

`--ends-ago` shifts the whole history so it stops that many hours before now,
which is what turns a recorded collapse into a silence long enough to act on.
The dashboard opens on an alarm seventeen hours old with *All is well*, *She
was out*, *This is normal now* and *Dealt with* underneath. Pressing one ends
the episode and tells everybody else who went.

Against real Ring events instead:

```bash
python -m stillwatch serve --live --db events.db --person Margarette
```

## Tests

```bash
python check.py
```

Thirteen suites. The detection rules are tested against days that must alert and
days that must not: a collapse, an outing of the same length, a lie in, a caller
at an empty house, and a camera that dies while the person is fine. The rest
cover the parts a test suite usually misses and this one did not: that every id
the page script reaches for exists in the page, that the commands run from the
folder they are actually run from, that the feed posts what it claims to post,
and that every state is readable as text in both themes.

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

It is online: **<https://stillwatch.tech>**

The recorded days are open to anyone. The household itself is behind a sign in,
because the dashboard says out loud when a real looking home is empty.

Ring requires four HTTPS addresses that the integrator hosts, so Stillwatch has
to be deployed before a real account can be linked.
[docs/DEPLOY.md](docs/DEPLOY.md) covers both ways of getting one: a small
server, where a single script installs Python, Postgres, the service and Caddy,
or a tunnel from a machine you already own.

## What it cannot do

Stillwatch reads a house, not a person. A camera cannot tell who walked past
it, so a home with more than one person living in it is outside what this can
judge: the second person's movement reads exactly like the first's.

The one part of that question the cameras can settle on their own is answered.
Nobody is in two rooms at the same second, so movement in two rooms seconds
apart is two people, and the day says so in words rather than quietly judging
the wrong one. Working out which cameras watch the way *between* rooms, so
that one person stepping through a door is not mistaken for two, is learned
from the household's own weeks rather than from what the cameras were named.

How quickly it notices depends on the household, and the spread is wide. Run
against six simulated homes, each judged on its own learned routine, the same
collapse at 09:15 was raised as urgent between 55 minutes and seven and a half
hours later. A person who is normally still for twenty minutes at a time is
noticed quickly; one who regularly reads for two hours is not, because the
whole method is to compare against that person rather than against a number
somebody chose. On the other side, one of those six homes raised an alert on
an ordinary day when nothing was wrong. Both numbers are in the test suite so
that a change which makes either worse fails there.

It holds no video and no images. It reads only that movement was seen and at
what time, which is also why it cannot tell somebody who has fallen from
somebody who is reading quietly. It can only say that the stillness has gone
on far longer than this person's own history says it should, and show the
working.

[docs/SECURITY.md](docs/SECURITY.md) covers what is worth attacking here and
what stops it.

## Documentation

| | |
|---|---|
| How it works, and why it is built this way | [docs/DESIGN.md](docs/DESIGN.md) |
| Every decision that was not obvious, and what it cost | [docs/DECISIONS.md](docs/DECISIONS.md) |
| Deploying it, and every setting | [docs/DEPLOY.md](docs/DEPLOY.md) |
| What is worth attacking here, and what stops it | [docs/SECURITY.md](docs/SECURITY.md) |
| The Ring developer experience, written as it happened | [docs/FRICTION_LOG.md](docs/FRICTION_LOG.md) |
| The considered view of every Amazon service used | [docs/PRODUCT_FEEDBACK.md](docs/PRODUCT_FEEDBACK.md) |
| The event simulator the engine was built against | [ring-event-simulator/](ring-event-simulator/), or [its own repository](https://github.com/kadian-arch/ring-event-simulator) |

The friction log and the product feedback are written for the teams whose
tools are used here. Each entry states what was attempted, what happened, how
much it cost, and what would have helped.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
