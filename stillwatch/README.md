# Stillwatch

Watches for the silence.

Every home monitoring product alerts when something happens. For a person
living alone the dangerous thing is that nothing does, and silence is exactly
what those products ignore.

## What is hard about it

The most common reason a house is quiet is that nobody is in it. A system that
phones a daughter at work every time her mother goes to the shops gets switched
off within a week.

So the problem is not detecting silence. It is telling apart:

- **Absent.** They left. The silence is expected and means nothing.
- **Unresponsive.** They are home and have stopped moving. The silence matters.

Devices are classified as **transit**, near a door, or **interior**, inside the
house. A stretch of interior silence that opens with a transit event is someone
leaving. The same stretch with no transit event is someone who stopped.

## The parts

Events arrive over a signed webhook, or are pulled in bulk when a Ring account
is first linked, and land in a store that speaks SQLite on a laptop and
Postgres in a deployment. A camera is either on the way out of the house or
inside a room, and the difference is what lets leaving be told from stopping.

From a household's own weeks the learner draws two things: how often each
camera sees that person at each hour of each kind of day, and how long they
normally stay still once they have settled at that hour. The monitor compares
the present against both and places it on a ladder. The dashboard shows the
ladder, the reasoning underneath it, and the day laid out camera by camera.

When a message is warranted, Amazon Bedrock writes its opening sentence and
Amazon SNS delivers it to whoever has subscribed. Neither is on the path
between noticing a silence and telling somebody: the engine decides, and the
engine's own sentence is used whenever the model is unreachable or writes
something the validator refuses.

## The learner

Two things come out of a household's history.

**Rhythm.** For each device, kind of day, and hour, the share of days with any
activity. This is the shape of a normal day for this person: when they get up,
when the kitchen is busy, when the house goes quiet.

**Tolerated quiet.** The longest silence this person normally produces, keyed by
the hour the silence began. Not the average gap between events, the longest
stretch, one sample per day per hour. An average would be a few minutes and
would fire constantly. Keyed by the start hour because a silence that begins at
eleven at night is still a night silence at three in the morning.

Two rules make those numbers mean something.

*Departures are excluded.* A silence that starts within half an hour of a door
event is a trip out, and it does not go into the baseline. Letting absences in
would raise the threshold until a real collapse fell underneath it. The test
suite proves this by learning the same history twice, once with the rule and
once without, and showing the thresholds move.

*Broken cameras are excluded.* A device that reported itself offline is dropped
from the denominator for those hours, so a fault never looks like a person who
stopped moving, and never drags down a device's learned rhythm.

**Anchors** are activities that happen on most days at about the same time, such
as the first movement of the morning. A missing anchor is worth more than
general quiet, because it is a deviation from this person rather than from an
average. Anything that wanders by more than three quarters of an hour is not an
anchor, since it cannot tell you anything is late.

## The ladder

| State | Meaning | What happens |
|---|---|---|
| NORMAL | Silence is within what she normally produces at this hour | Nothing |
| QUIET | Longer than usual, but plausible | Dashboard only |
| CONCERN | Well past normal, with no door event to explain it | Notify, low urgency |
| ALERT | Far past normal, or past normal with a habit missed | Notify, urgent |
| AWAY | A door opened as the silence began | Suppressed, shown as out |
| UNKNOWN | Every interior camera is offline, or there is no baseline yet | Say so, never guess |

It comes back down, and AWAY sits beside the ladder rather than on it.

Five rules stop the ladder crying wolf, each of which came out of running a
scenario and watching it get the answer wrong.

*Only a door event at the start of a silence explains it.* A caller ringing the
bell three hours into a collapse leaves the alert exactly where it was.

*A missed habit lifts concern to alert, never quiet to concern.* Someone
sleeping in is late for every habit they have and perfectly fine.

*Being out longer than usual is shown, not shouted.* Somebody spending the day
at their sister's must not set off a phone. Only a full day with no return
escalates.

*A camera that was offline cannot have missed a habit.* Its silence says nothing
about the person.

*A dead camera in a room she normally uses at this hour holds the alarm back to
concern and names the camera to check.* If we cannot see the room she would be
in, that is a likelier explanation than a collapse. It applies only to a camera
she is normally active in front of, otherwise one flaky camera anywhere in the
house would switch off alerting altogether.

Every assessment carries the reasoning that produced it, because a caregiver who
cannot tell why their phone went off learns to ignore it.

## Telling someone

The monitor judges the house every five minutes, and almost none of those
judgements should reach a phone.

| When | Message | Urgency |
|---|---|---|
| Concern has held for 15 minutes | One per quiet spell | Low |
| Alert is reached | Straight away, even if the low one went | Urgent |
| Alert continues | A reminder every hour | Urgent |
| She moves again, after anyone was told | All clear | Information |
| Every camera inside goes offline | Once, saying we cannot see | Low |
| Out, quiet, or normal | Nothing, ever | |

A low urgency message can wait a quarter of an hour to be sure; an urgent one
never waits. Nothing counts as sent until a channel that reaches a person has
accepted it, so a failed send is retried on the next assessment instead of an
alert being lost to a network blip. A log file accepting a message does not
count as a caregiver hearing it.

Delivery is through Amazon SNS. Caregivers subscribe to a topic by email or
text message, so Stillwatch never holds their contact details. Configure with
environment variables:

| Variable | Meaning |
|---|---|
| `STILLWATCH_SNS_TOPIC_ARN` | The topic to publish to |
| `AWS_REGION` | Its region |
| `STILLWATCH_SNS_ENDPOINT` | Optional, for LocalStack |
| `STILLWATCH_NOTICE_LOG` | Optional, a file that records every message |

With nothing configured, messages are printed rather than sent anywhere.
SNS needs `pip install boto3`; nothing else does.

## The sentence a family actually reads

Stillwatch knows exactly why it is worried, and what it produces on its own is
correct, complete and cold. A daughter reading it at three in the morning does
not need a report.

Amazon Bedrock rewrites the opening sentence. It is given the facts and nothing
else, and what it returns is checked before anyone sees it:

- Every number in the message must appear in the facts. A model that invents a
  time is refused.
- A short list of words it may never use: alert, system, detected, monitoring,
  anomaly, status, trigger.
- Two sentences, no greeting, no sign off, no telling anyone how to feel.
- If the model is slow, silent, or refused, the engine's own sentence is sent
  instead and nothing is lost.

The reasoning underneath the opening line is never touched by the model. It is
the engine's, and it stays exactly as the engine produced it.

**The model makes the message human. It can never make it wrong.**

Set `STILLWATCH_BEDROCK_MODEL_ID` to switch it on, and `AWS_REGION` alongside
it. With nothing set, messages read exactly as they did before.

## Why not a neural network

A caregiver woken at three in the morning needs to know why their phone went
off. "No movement in the kitchen since 19:20, and she is normally active there
until 22:00 on a Tuesday" is actionable. A confidence score from a model nobody
can interrogate is not. In caretaking, explainability is a product requirement.

Frequency baselines and gap percentiles work on the amount of history that can
realistically be collected, and every number in an alert can be traced back to
the days it came from.

## Run it

Generate a history with the simulator, then learn from it:

```bash
cd ../ring-event-simulator
python -m ringsim --scenario normal --days 28 --out ../stillwatch/data/normal.jsonl --manifest ../stillwatch/data/manifest.json

cd ../stillwatch
python -m stillwatch learn --events data/normal.jsonl --manifest data/manifest.json --out data/baseline.json
```

The report prints the anchors it found, an hour by hour rhythm grid per device,
and the tolerated quiet per hour in plain sentences.

Then watch a day unfold against what was learned from the days before it:

```bash
python -m stillwatch watch --events data/fall.jsonl --manifest data/manifest.json --why
```

## The dashboard

```bash
pip install -r requirements.txt
cd ..
python demo_data.py
cd stillwatch
python -m stillwatch serve
```

Then open http://127.0.0.1:8420.

Pick a day and press Play, and the judgement changes as the day unfolds. Put
*Collapse at home* next to *Out for the day*: the same silence, read two
different ways, is the whole idea.

The address bar carries the day and the moment, so a link opens on the frame
you were looking at. Space plays and pauses, and the arrow keys step five
minutes at a time, or an hour with Shift held, without needing the slider.

The page shows one sentence first, set as a note rather than a system message,
because that sentence is the product. Everything under it exists to back it up:
how long it has been quiet against what is usual, today's activity laid over her
normal rhythm, the habits she has kept or missed, and which cameras are working.
Times are always the household's own clock, never the viewer's.

## Tests

```bash
python ../check.py
```

Runs all five suites and checks the browser script parses. Individually:

```bash
python tests/test_rhythm.py
python tests/test_monitor.py
python tests/test_notify.py
python tests/test_service.py
```

## Working against real Ring data

Nothing here imports the simulator. Events arrive through `normalise` in
`model.py` and devices through `load_roster`, and those two functions are the
only things that know where the data came from.
