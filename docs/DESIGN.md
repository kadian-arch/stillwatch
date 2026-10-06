# Stillwatch: design

## The problem

Every home monitoring product alerts on events. Motion detected. Doorbell
pressed. Someone at the door.

For a person living alone, that is the wrong signal. The dangerous thing is not
that something happened. It is that nothing did. A fall in the bathroom at 6am
produces no motion event, no doorbell press, no alert. It produces silence, and
silence is exactly what every existing system ignores.

Stillwatch watches for the silence.

## The core problem nobody solves

A naive version of this is a timer: no events for N hours, send an alert. That
fails immediately, because the most common reason for a quiet house is that
nobody is in it. A system that phones a daughter at work every time her mother
goes to the shops gets switched off within a week.

So the real problem is not detecting silence. It is telling these two apart:

- **Absent.** They left. The silence is expected and means nothing.
- **Unresponsive.** They are home and have stopped moving. The silence matters.

Getting that distinction right is the whole product. Everything else is
plumbing.

## How we tell them apart

Ring gives us motion events with device locations and doorbell presses. Devices
near an entrance produce a different signal from devices inside.

We classify each device as **transit** (front door, entryway, anything that sees
someone leaving or arriving) or **interior** (kitchen, hallway, living room).

Then the rule is a sequence, not a threshold:

| Last transit event | Interior activity since | Reading |
|---|---|---|
| Recent | None | Probably out. Hold. |
| None today | None for an unusual stretch | Home and inactive. Escalate. |
| Recent, then interior activity resumed | Normal | Came home. Normal. |

This is inference, not certainty, so the interface says what it believes and
why. A caregiver seeing "no interior movement since 07:40, and the front door
has not opened today" can judge for themselves. A caregiver seeing "ALERT"
with no reasoning learns to ignore it.

## Learning what normal looks like

"Unusual stretch" has to mean something specific per person. Eight hours of
silence at 2am is a good night's sleep. Eight hours at 2pm is not.

We learn two things from event history:

**1. Rhythm.** For each combination of device, day type (weekday or weekend) and
hour, how often is there activity at all? This gives the shape of a normal day
per person: when they get up, when the kitchen is busy, when the house goes
quiet.

**2. Expected silence.** For each day type and hour, what is the distribution of
gaps between consecutive events? We take a high percentile as the threshold for
"longer than this person's normal". Night-time naturally tolerates long gaps
because the history says it should.

**Anchors** are the strongest signal. Some activity happens nearly every day at
roughly the same time: first movement of the morning, kitchen activity around a
meal. A missing anchor is worth more than general quiet, because it is a
deviation from this specific person's habit rather than from an average.

## Why this is deliberately not a neural network

A sequence model would be the obvious choice, and it is the wrong one here.

A caregiver woken at 3am needs to know why their phone went off. "No movement in
the kitchen since 19:20, and she is normally active there until 22:00 on a
Tuesday" is actionable. A confidence score from a model nobody can interrogate
is not. In caretaking, explainability is a product requirement, not a
nice-to-have.

Frequency baselines and gap percentiles are simple, they work on the amount of
history we can realistically get, and every alert can state its own reasoning in
one sentence.

## Escalation

Alerting is a ladder, not a switch.

| State | Meaning | Action |
|---|---|---|
| NORMAL | Activity matches the learned rhythm | Nothing |
| QUIET | Silence longer than usual, but plausible | Dashboard only |
| CONCERN | Silence well past normal, no transit event to explain it | Notify, low urgency |
| ALERT | Concern sustained, or a missed anchor on top | Notify, urgent |
| AWAY | Transit event then sustained silence | Suppressed, shown as away |
| SETTLED | Somebody has been round and said what they found | Stops, and tells the others who went |
| UNKNOWN | Nothing has reported, so nothing can be judged | Says so, and never guesses |

Three properties that matter. It can go back down. AWAY suppresses the ladder
entirely rather than silencing it, so the dashboard still shows what it thinks.
And SETTLED is the only thing other than movement that ends an episode, because
a daughter who has already telephoned and found her mother well needs a way to
say so that stops everybody else being asked.

## Architecture

```
Ring API  ───┐
             ├──> ingest ──> event store ──> rhythm learner
simulator ───┘       ^                                      |
                     |                                      v
              webhook receiver                       silence monitor
              (public HTTPS)                                |
                                                            v
                                                    state machine
                                                            |
                                              ┌─────────────┴────────┐
                                              v                      v
                                          dashboard            notifications
```

**Stack:** Python and Flask for the service, plain HTML, CSS and JS for the
dashboard with no build step. No official Ring SDK exists, so API calls are
plain HTTP.

The event store speaks SQLite and Postgres through one small dialect seam: the
parameter placeholder and how a connection is opened are the only things that
differ, and every statement is written so both understand it. SQLite is for
running it on a laptop. Deployed, it is Postgres, because a container's disk is
wiped on every restart and a baseline needs weeks of history.

**The simulator is built first.** It emits events in Ring's documented shape:
motion, doorbell press, device online and offline, with timestamps and device
ids. The whole engine is developed and tested against it. When Ring access
lands, the source swaps and the engine does not change.

This is the same reason FairGlass had a mock proof mode. Nothing downstream
should ever be blocked on an external dependency we do not control.

## Where Amazon's services sit, and where they do not

**Amazon Bedrock** writes the opening sentence of a message, turning the
reasoning into something a person reads at a glance rather than a table of gap
percentiles. It is given the facts and nothing else, and what comes back is
checked before anyone sees it: too long, more than one paragraph, a word the
product does not use, a number that is not in the facts, or a sentence that
reads as though nobody proofread it, and the engine's own sentence goes
instead.

It is deliberately not on the path between seeing a silence and telling
somebody about it. A model that is slow, unreachable or simply wrong costs a
message its tone and none of its facts.

**Amazon SNS** delivers it. Caregivers subscribe themselves to the topic, so
Stillwatch publishes to one address and never holds a phone number or an email
for anybody. That is a privacy property, not a convenience: there is no
contact list here to leak.

**The simulator** is why any of the rest could be built. There is no official
Ring SDK, and the sandbox will not emit events on demand, so a standalone
generator of realistic multi-week histories was the only way to develop and
test an engine that reasons about weeks. It lives in
[ring-event-simulator/](../ring-event-simulator/) here and separately at
[kadian-arch/ring-event-simulator](https://github.com/kadian-arch/ring-event-simulator), under MIT, with its own tests, and is
useful to anyone else building on the Ring API.

## When Stillwatch itself breaks

A dead service looks exactly like a quiet house. A page that fails to load
looks exactly like a page with nothing to report. Both of those read as
reassurance, which makes them the two most dangerous states this software has.

So every failure is handled under one rule: a fault in Stillwatch must be
visible as a fault in Stillwatch, and must never be phrased as, or mistakable
for, a reading of the home.

**The cameras stop reporting.** Judged before the inactivity ladder is
considered at all, not after. If nothing has been heard from the cameras for
longer than the contact gap, the state is UNKNOWN with the reason `no_contact`,
and the wording says that this is a problem with the connection and not
necessarily with anyone at home. A house that cannot be observed is not a
house that is known to be still.

**Every interior camera is offline.** UNKNOWN with the reason `blind`, naming
the cameras that are down. Partial blindness is handled differently and more
carefully: a camera that is down cannot have seen the habit it usually sees, so
its silence is excluded from the rhythm rather than counted as stillness, and a
dead camera in a busy room holds the alarm back while a dead camera in a room
nobody uses does not.

**The database is unreachable.** The health endpoint is the one thing that must
keep answering, because a health check that returns an error says only that
something is wrong somewhere. It answers 503, reports `ok: false` and
`store_ok: false`, and still says whether the webhook is armed, so the part
that is down can be named without reading the database to find out. Other API
requests fail with a sentence that says the fault is Stillwatch's own and is
not a reading of the home. The dashboard shows that sentence rather than
wording that blames the day.

**A notification channel is down.** Each channel is tried independently and its
failure recorded against the notice with the channel's name. Judging continues,
the watcher still records its tick and its state, and the alarm that could not
be sent is still on the dashboard for anyone who opens it. An undelivered alarm
is not a cancelled alarm, and the reminder ladder keeps trying.

**The model refuses to write the sentence.** Amazon Bedrock is deliberately off
the detection path. If it fails, or writes something that does not pass the
checks on it, the notice goes out in the deterministic wording instead. Nothing
about whether to alarm depends on it.

**The watcher quietly dies.** The one failure that this product cannot afford,
because it is indistinguishable from a household where nothing is wrong. Every
tick writes the time it ran and what it decided, whether or not anything was
sent, and something outside the process reads that and complains when it stops
moving.

**Deliveries arrive out of order.** Not a failure mode here, by construction. A
judgement is a function of the whole stored set read in time order, every event
carries the time the camera saw it, and every event has an id derived from its
own content. Arrival order is not an input, so it cannot be a fault. Because
that is a claim rather than an obvious fact, it is tested: a day delivered
backwards, and in twenty five shuffles, must produce a byte-identical reading,
and the transit events arriving last must not turn an outing into an alarm.

## What it does, and what it does not

It ingests events over a signed webhook and backfills history on first link.
It learns a household's rhythm per device, per kind of day, per hour. It tells
a camera on the way out from a camera inside a room, and uses the difference to
tell leaving from stopping. It judges the present on a ladder, shows the
reasoning on a dashboard, writes the message, and delivers it. Its detection
rules are tested against days that must raise an alarm and days that must not.

**One household at a time.** A deployment watches one person. Nothing in the
model prevents more, but nothing has been built for it either.

**It watches a house, not a person.** A camera cannot tell who walked past it,
so a home with two people in it is outside what this can judge. Where two rooms
move within seconds of each other it says so on the day rather than quietly
judging the wrong one, and it learns which cameras watch the way between rooms
so that one person stepping through a door is not mistaken for two.

**No video, no images, ever.** It reads only that movement was seen and when.
That is the reason it cannot tell somebody who has fallen from somebody who is
reading quietly, and it is also the reason it can be pointed at a parent's home
without anybody feeling watched. The second is worth more than the first.

**No mobile application.** The dashboard is responsive, and the thing that
matters arrives as a message anyway.
