# Contributing

Stillwatch decides whether a quiet house means somebody left or somebody
stopped. Everything here serves that one judgement, and the most useful
contribution is usually a household this cannot read correctly yet.

## Running it

```bash
pip install -r stillwatch/requirements.txt
python demo_data.py
python check.py
```

`check.py` runs every test suite and the static checks on the page, the
palette and the two entry points. It generates the recorded days first if they
are not there, so a fresh clone needs nothing else. It exits non zero when
anything is wrong, which is all CI needs.

To see it running:

```bash
cd stillwatch
python -m stillwatch serve
```

Then open <http://127.0.0.1:8420>.

## What is most wanted

**Households this reads wrongly.** The engine assumes one person. A visitor, a
second occupant, a night shift or a routine that shifts after an illness can
all look like something they are not. A failing case written as a scenario is
worth more than a fix, because it says what correct would have been.

**Better transit and interior classification.** Telling absence from
unresponsiveness rests entirely on knowing which cameras watch a way out.
Today that is learned from where movement tends to lead. Anyone with real
household layouts who can improve it improves the whole product.

**Ways in besides Ring.** The adapter in `stillwatch/stillwatch/ring.py`
normalises one vendor's events into a small internal shape. Nothing above it
knows where events came from, so another source is a new adapter rather than a
change to the engine.

## What to keep true

**Nothing looks at video.** The product reads when a camera saw movement and
when it did not. It never requests footage, never stores a frame and never
analyses an image. This is the reason a family will put it in a bedroom
corridor, and it is not a trade to make later for accuracy.

**Every judgement says why.** A state with no sentence behind it is not
shippable here. Somebody is being asked to phone their mother at six in the
morning, and they are owed the reason.

**Event ids come from the event.** The id is a hash of the device, the kind
and the timestamp, never a counter. Ingestion has to be safe to repeat, and a
counter makes two runs produce different ids for the same event.

**Order does not change the verdict.** The judgement is a function of the set
of events, not of the sequence they arrived in. Webhooks retry and arrive out
of order. `stillwatch/tests/test_delivery.py` shuffles deliveries and insists
the answer is the same.

**A failure never reads as a quiet house.** This is the one that matters most.
If the feed dies, the store is unreachable or the judge stops running, the
product must say it cannot tell. Reporting calm because nothing arrived is the
worst thing this software can do, and several of the checks exist only to
prevent it.

## Style

Follow what is there. Comments explain why, not what. Where something was a
judgement call, say what the call was, because the next person will wonder.
Code is plain Python, no framework beyond Flask, and the dashboard is HTML and
CSS with no build step.

## Submitting

Open an issue first for anything larger than a scenario or a fix, so the shape
can be agreed before you write it. Keep `python check.py` passing. If you
change behaviour, add the check that would have caught the old behaviour.

## Security

Please do not open a public issue for a vulnerability. Report it privately
through GitHub's security advisories on this repository, or to the maintainer
at <donalsienkum@gmail.com>. [docs/SECURITY.md](docs/SECURITY.md) describes
what the service already defends against.
