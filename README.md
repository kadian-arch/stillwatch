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

Open <http://127.0.0.1:8420>, pick a day, and press **Play**. Put *Collapse at
home* beside *Out for the day* to see the two judged differently at the same
moment.

### Seeing it raise an alarm

The recorded days replay an alarm but cannot be answered, because answering is
something you do about today. To get a live household that is actually in
trouble, and the buttons that go with it, build a collapse that happened
yesterday morning and serve it as today:

```bash
cd ring-event-simulator
python -m ringsim --scenario fall --days 40 --start 2026-08-24   --out fall.jsonl --manifest fall.json
cd ../stillwatch
python -m stillwatch ingest --events ../ring-event-simulator/fall.jsonl   --manifest ../ring-event-simulator/fall.json --db alarm.db
STILLWATCH_TZ=Africa/Douala python -m stillwatch serve --live --db alarm.db   --person Margarette
```

`--start` is thirty nine days before the day the collapse should fall on, so
the stream ends then. The dashboard opens on an alarm seventeen hours old,
with *All is well*, *She was out*, *This is normal now* and *Dealt with* under
it, and pressing one settles the episode and tells everybody else who went.

Against real Ring events instead:

```bash
python -m stillwatch serve --live --db events.db --person Margarette
```

## Tests

```bash
python check.py
```

Twelve suites. The detection rules are tested against days that must alert and
days that must not: a collapse, an outing of the same length, a lie in, a caller
at an empty house, and a camera that dies while the person is fine. The rest
cover the parts a test suite usually misses and this one did not: that every id
the page script reaches for exists in the page, that the commands run from the
folder they are actually run from, and that every state is readable as text in
both themes.

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

It holds no video and no images. It reads only that movement was seen and at
what time, which is also why it cannot tell somebody who has fallen from
somebody who is reading quietly. It can only say that the stillness has gone
on far longer than this person's own history says it should, and show the
working.

[docs/SECURITY.md](docs/SECURITY.md) covers what is worth attacking here and
what stops it.

## Hackathon submission

Built for **Build, Ship, Shape: Amazon Developer Hackathon**.

| | |
|---|---|
| Track | Ring |
| Mini challenge | AWS Builder, through Amazon Bedrock for the wording of an alert and Amazon SNS for delivering it |
| Mini challenge | Open Source, through [ring-event-simulator](ring-event-simulator/) |
| Developer feedback | [docs/PRODUCT_FEEDBACK.md](docs/PRODUCT_FEEDBACK.md), [docs/FRICTION_LOG.md](docs/FRICTION_LOG.md) |
| How it was built, and why | [docs/DESIGN.md](docs/DESIGN.md), [docs/DECISIONS.md](docs/DECISIONS.md) |

The friction log records the Ring developer experience as it happened, with a
severity and a suggested fix for each problem encountered. The product feedback
is the considered view of every Amazon tool used, including what worked.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
