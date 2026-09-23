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

Two properties that matter: it can go back down, and AWAY suppresses the ladder
entirely rather than silencing it, so the dashboard still shows what it thinks.

## Architecture

```
Ring API  ───┐
             ├──> ingest ──> event store (SQLite) ──> rhythm learner
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

**Stack:** Python and Flask for the service, SQLite for events, plain HTML, CSS
and JS for the dashboard with no build step. No official Ring SDK exists, so
API calls are plain HTTP.

**The simulator is built first.** It emits events in Ring's documented shape:
motion, doorbell press, device online and offline, with timestamps and device
ids. The whole engine is developed and tested against it. When Ring access
lands, the source swaps and the engine does not change.

This is the same reason FairGlass had a mock proof mode. Nothing downstream
should ever be blocked on an external dependency we do not control.

## Mini challenges, both earned rather than bolted on

**AWS Builder.** Two natural fits. Bedrock turns the anomaly signals into a
sentence a caregiver can read at a glance instead of a table of gap
percentiles. SNS delivers the notification to a phone. Both are documented
integrations doing real work.

**Open Source.** The simulator is the contribution. There is no official Ring
SDK and the sandbox is limited, so a standalone event simulator is genuinely
useful to anyone else building on the Ring API. It ships as its own repo with a
licence. The thing we build to unblock ourselves becomes the entry.

## Scope

**Must ship.**

- Event ingestion: webhook receiver plus history backfill
- Simulator producing realistic multi-week histories
- Rhythm learner: per-device, per-daytype, per-hour baselines
- Transit and interior classification, away detection
- Silence monitor and the state ladder
- Dashboard showing learned rhythm, current state, and the reasoning
- Test suite covering the detection logic, including the false positive cases

**Stretch, only once the above is solid.**

- Bedrock alert narration
- SNS delivery
- Multiple monitored people

**Explicitly out.**

- Video or image analysis. The whole point is that we do not need to watch
  anyone to know they are alright, which is also a privacy argument worth
  making.
- Mobile app. The dashboard is responsive and that is enough.

## The demo, under 3 minutes

1. Margaret's learned week. This is what her normal looks like.
2. Today, no kitchen activity by 09:00. She is normally up by 07:30.
3. The ladder moves QUIET to CONCERN, and states its reasoning.
4. The front door has not opened. She is home, not out.
5. Alert, with the sentence a caregiver actually reads.
6. Then the contrast: same silence, but the door opened at 08:10. AWAY.
   No alert. This is the part that makes it a product rather than a timer.

Beat 6 is the one that wins or loses the video, so it goes in even if something
else gets cut.
