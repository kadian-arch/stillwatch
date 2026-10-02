# Decisions

Why Stillwatch works the way it does. Most of these came from watching a
scenario produce the wrong answer, not from planning.

---

## 1. The product is telling absence from unresponsiveness

A silence timer is trivial and useless, because the most common reason a house
is quiet is that nobody is in it. Everything else here exists to serve one
distinction: they left, or they stopped.

Cameras are classified as **transit** (watching a way in or out) or **interior**.
A stretch of interior silence that opens with a transit event is somebody
leaving. The same stretch with no transit event is somebody who stopped.

*Prevents:* the system that phones a daughter every time her mother goes to the
shops, and gets switched off within a week.

---

## 2. Tolerated quiet is the longest daily silence, not the average gap

With four interior cameras, the gap between consecutive events averages a couple
of minutes, and its ninety fifth percentile is around a quarter of an hour.
Alerting on that would fire ten times a day.

The question actually being asked is how long the house has been silent, so the
baseline describes the longest stretch a person normally produces: one sample
per day per hour, not one per gap.

---

## 3. A silence is keyed by the hour it began, not the hour it is noticed

A silence that starts at eleven at night is still a night silence at three in
the morning. Looking it up under three would compare it against a waking hour
and raise an alarm over somebody sleeping.

---

## 4. Time out of the house is excluded from the baseline

A silence that opens within half an hour of a door event is a trip out, and it
never enters the baseline.

*Prevents:* thresholds creeping upward every time somebody goes shopping, until
a real collapse fits underneath them. The test suite learns the same history
twice, with the rule and without, and shows the daytime thresholds move.

---

## 5. A camera that was offline is dropped from the denominator

A device that reported itself down cannot have seen anything, so those hours do
not count against its learned rhythm, and its silence is never read as a person
who has stopped moving.

---

## 6. A habit is an onset, not the first reading after a window opens

Learning "first activity in the late morning" produced a habit of arriving in
the living room at 11:04, give or take three minutes. That was not a habit. It
was somebody already sitting there when the clock passed eleven.

A habit is now the first movement after at least half an hour of stillness on
that camera, so only things that genuinely start at a time are learned.

---

## 7. A missed habit lifts concern to alert, and never lifts quiet to concern

Somebody who sleeps in is late for every habit they have and perfectly fine. The
escalation only applies once the silence is already well past normal.

---

## 8. A missed habit only counts if it fell due during the silence

A habit missed before the current silence began says nothing about it. Somebody
who slept through their usual morning and was busy all afternoon is not more
worrying when they sit still after lunch.

*Found by:* checking every five minutes instead of every fifteen, which exposed
a false alert that lasted a single reading.

---

## 9. Being out longer than usual is shown, not shouted

An unusual absence is flagged on the dashboard and stays off the escalation
ladder. Only a full day with no return escalates.

*Prevents:* somebody spending the day at their sister's setting off a phone.

---

## 10. Only a door event at the start of a silence explains it

A doorbell press three hours into a collapse is a caller, not somebody getting
up. The alert stays exactly where it was, and there is a test that injects one
to prove it.

---

## 11. A dead camera in a room somebody normally uses holds the alarm back

If the room they would be in cannot be seen, that is a likelier explanation than
a collapse, so the state is held at concern and the camera is named.

This applies only to a camera they are normally active in front of at that hour.
An earlier version held back on *any* offline camera, which meant one flaky
camera anywhere in the house could switch off alerting altogether.

---

## 12. Not a neural network

A caregiver woken at three in the morning needs to know why their phone went
off. "No movement in the kitchen since 19:20, and she is normally active there
until 22:00 on a Tuesday" is actionable. A confidence score from a model nobody
can interrogate is not.

In caretaking, explainability is a product requirement rather than a preference.
Frequency baselines and gap percentiles also work on the amount of history that
can realistically be collected, and every number in an alert traces back to the
days it came from.

---

## 13. No video analysis, ever

Nobody has to be watched to know they are alright. The absence of movement
carries the whole signal. The integration requests motion and doorbell scopes
only.

---

## 14. The engine never knows where events came from

Events enter through one normalise function and devices through one roster
loader. Ring's vocabulary is translated in exactly one file, which is why the
same engine runs against a simulator and against real hardware without changing.

*Consequence:* the entire product was built and tested before any API access
existed.

---

## 15. Writes are idempotent on the event id

Ring redelivers a webhook it is not certain was received. A duplicated motion
event would shorten a silence that never actually broke, which in this product
means a missed alert.

Events arriving without an id are given a deterministic one derived from device,
type and timestamp, so a redelivery still collides with the original.

---

## 16. A pagination link is data, not an instruction

History pagination is undocumented, so the client follows the JSON:API `next`
convention when it is offered, and refuses any link pointing at a host other
than Ring's own API. Following one blindly would send an access token wherever
the reply named.

---

## 17. Postgres in production, SQLite on a laptop

A hosted container's disk is wiped on every restart, and a baseline needs weeks
of history to mean anything. The store picks its database from the connection
string; the SQL is written once.

---

## 18. A model may make the message human, never make it true

Amazon Bedrock rewrites the opening sentence of a notification. It receives the
facts and nothing else, and what it returns is checked before anyone sees it:
every number must appear in the facts, a short list of words is forbidden, and
length is capped. Anything refused falls back to the deterministic sentence.

The reasoning underneath the opening line is never touched, so what a caregiver
can verify is never paraphrased.

---

## 19. Times belong to the household, not the viewer

Timestamps render in the clock of the home being watched. Rendering them in the
viewer's time zone would put a caregiver abroad an hour or more out from what
the house actually saw.

---

## 20. Nothing counts as sent until a person could have received it

A log file accepting a message is not a caregiver hearing it. Delivery is only
recorded when a channel that reaches people accepts it, so a failed send is
retried on the next assessment rather than an alert being lost to a network
blip.

---

## 21. The state colours were measured, not chosen

The first palette put concern and alert 14 units apart in perceptual distance.
Anything under 15 is hard to separate even with ordinary colour vision, and
those two states are the whole product: one means keep watching, the other
means go round there now.

They were re-stepped to a yellow and a crimson, which measure 24 apart in
normal vision and 17 under the commonest form of colour blindness. The grey
used for "cannot tell" reads as grey on purpose, and carries a texture so it
is never distinguished by colour alone.

*Consequence:* the amber sits below the usual contrast ratio against white.
That is allowed only where the reader has words as well as colour, so every
state is spelled out in the hero, in the legend, and in the tooltip on each
segment.

---

## 22. Severity is height before it is colour

The timeline ribbon draws normal low and alert full height, with quiet and
concern between them. Someone can read how bad the day got from the silhouette
with the colour removed entirely.

A long alert is also a large area, and a large area of saturated colour reads
as a warning banner rather than as a chart, so the body of each band is a
twenty percent tint with the full colour kept for the three pixel cap along
its top edge.


---

## 23. An answer ends a stretch of worry, and movement is no longer the only thing that can

Until a caregiver could say something, the only way out of an alert was the
person moving again. A daughter who had already phoned and found her mother
perfectly well had no way to tell Stillwatch so, and it chased her for two
days. More than eighty messages about a single stretch of quiet, which is not
urgency, it is noise.

Four answers now end it: all is well, she was out, this is normal now, dealt
with. The reading underneath does not change and is still on the page. What
changes is the question being asked.

*Consequence:* "this is normal now" is also training data. The stretch is
recorded as a confirmed quiet spell for that hour and day type and folded into
the baseline, so somebody whose sleeping has changed stops being asked about
in the second week rather than the tenth.

---

## 24. Events are named by what they are, not by the order they were made in

Simulated events carried an id that was a counter, starting at one on every
run. Two runs covering different stretches of time therefore handed out the
same ids to different events, the store refused every one of them as a
redelivery, and a feed posting hundreds of events an hour wrote nothing at
all.

Nothing failed. There was no error to find: the poster reported success, the
service reported a healthy webhook, and the dashboard showed a house where
nobody had moved for four days. Exactly the picture this product exists to
raise the alarm about, produced by its own plumbing.

Ids are now a hash of the moment, the camera and the kind. The same event is
always the same event, and two different events are never the same one.

*Consequence:* ids no longer sort into time order. The test that asserted they
did was asserting an accident of the old scheme, and now asserts what actually
matters: that a second run reuses the ids of the first, and that an
overlapping window reuses none of them.

---

## 25. A passcode is optional, and running without one is announced

A dashboard that shows when a house is empty is a burglary planner, and the
answer buttons let anybody silence a real alarm. Both now sit behind a
household passcode.

It is deliberately possible to run with no passcode at all, because a product
nobody can get into is not demonstrable. What is not possible is doing that
quietly: with no passcode set, the page itself carries a panel saying anyone
with the address can read this home and answer on the family's behalf.

*Consequence:* the recorded days stay open whatever the setting, so somebody
can always see the engine work without being let into a household.

---

## 26. Which cameras watch the way between rooms is learned, not named

Two rooms moving at the same second is two people, because nobody is in two
rooms at once. That is the only question about company a motion sensor can
answer on its own, and it matters, because this watches a house and not a
person.

Measured against twenty-eight days of a household living alone, the rule fired
thirty-one times. Every single one involved the landing. A camera on a landing
sees you as you step out of a bedroom, so the landing and the bedroom move
together every time one person walks through a door.

So the baseline learns which cameras do that: any camera that fires within
five seconds of two or more different others is watching a passageway, and
pairs involving it are not evidence of anything. With the landing excluded,
the same twenty-eight days produce no claim of company at all.

*Consequence:* the same trick as the doorbell. Ring's API carries no camera
type and a household naming one "Landing" is a convention rather than a fact,
so the behaviour is read instead of the label.

---

## 27. Something watches the watcher

Every other part of Stillwatch reports on the household and nothing reported on
Stillwatch. The failure that costs the most is the quietest one: a judge that
has stopped running and a house where nothing is wrong look identical from
outside, and the only sign is an absence.

The watcher now writes the time of each judgement to the store, and
`stillwatch watchdog` reads that and the time of the last delivery and says so
if either has gone stale. It runs on a schedule, beside the feed.

*Consequence:* it says each fault once a day. The lesson of the eighty messages
applies to the watchdog more than to anything else, because an alarm about the
plumbing is the first one a reader learns to ignore.

---

## 28. A code each, not a code for the house

The first gate was one passcode for the household. It kept strangers out,
which was the urgent part, but it made the name beside an answer a claim
rather than a record: anybody holding the family's one secret could sign in as
anybody, so "Lucie said all was well" meant only that somebody with the
code said so.

Naming the people and giving each of them a code of their own costs one
setting and makes the name mean something. Nobody can sign in under a name
that is not on the list, the spelling recorded is the household's own rather
than whatever was typed, and taking somebody off is deleting their line.

The shared code is still there for a family that wants the simpler
arrangement, and the service says which of the two it is running.

*Consequence:* neither is multi factor, and a care service answering for many
homes would want an identity provider rather than either. That is written
down in the security model instead of being left to be discovered.

---

## 29. The engine's vocabulary is not the family's

"A quiet spell beginning around 16:00 normally ends within 26 min" is
accurate, and it asks the reader to work out what a spell is before they can
read the sentence. At three in the morning they will not.

It says "the longest she normally stays still at that time of day is 26 min"
now. Same number, same meaning, nothing to decode. "No baseline has been
learned for a weekday at 08:00" became "Stillwatch has not watched enough
weekdays around 08:00 to know what is ordinary for her then".

*Consequence:* the test that guarded the old sentence asserted the word
"baseline" appeared in it. It now asserts the opposite, that the machinery is
not named, which is the thing actually worth holding on to.

---

## 30. A camera's own hours, drawn beside its name

The camera list was a list of names. Every camera already carries twenty four
numbers saying how often it sees her in each hour of a day, learned from her
own weeks, and the dashboard was throwing them away.

Drawn as a strip beside each name, the kitchen is a morning and an evening,
the landing is the middle of the night, and the back door is almost nothing.
Scaled against each camera's own busiest hour rather than against the house,
so a quiet hallway still has a shape.

*Consequence:* it is the clearest evidence on the page that the thresholds
were learned rather than typed in, which is the claim the whole product rests
on and was previously only made in words.

---

## 31. The command line is a surface, and it needed a suite of its own

Eight suites were green while `notify-test` crashed on an attribute that has
never existed. The line it crashed on only runs when Amazon Bedrock is
unreachable, which is the exact moment somebody is setting Bedrock up and the
exact moment they need to be told why.

Nothing covered it because nothing ran the commands. Everything underneath
them was tested thoroughly; the thing a person actually types was not tested
at all.

Writing that suite found a second bug within the hour. The guard in the same
command that refuses to run when no notification channel is configured could
never fire, because a run with nothing configured is given a console channel
automatically, and the guard asked the channels rather than the configuration.
It had been passing silently since the day it was written.

*Consequence:* the cheap paths run as real subprocesses, because what is worth
proving is that the command works when typed. Anything that would reach the
network is called in process with the model stood in for. Error branches get
the most attention of all, because they run rarely and always at the worst
possible moment.

## 32. The message that proves delivery is written like a real one

The command that proves a caregiver can be reached used to send this:

    This is a test of the alert path for Margarette.
    - If this reached you, a real alert would too.
    - Nothing is wrong. Nobody needs checking on.

It is addressed to the developer. Everybody else on the topic is a caregiver
who did not ask for it, reading it on a phone beside messages that do mean
something, and the first thing it tells them is that they are looking at
somebody's test.

It now comes from the same place every other message comes from, in the same
shape: a lead sentence, then the reasons. It says what it is, that nothing has
happened, and what a real notice would say instead, so that a person who gets
one later recognises the difference.

    Stillwatch is checking that it can still reach the people who have asked
    to be told about Margarette's home.

    - Nothing has happened. The home is being watched as normal, and there is
      nothing to do.
    - A real notice arrives by this same route and reads differently. It names
      what was last seen and how long ago, and it asks somebody to check in.
    - Sent automatically at 11:19 on 02 October 2026.

*Consequence:* it carries INFO urgency, not URGENT. A message whose own first
line says nothing is wrong has no business being labelled urgent, and a
caregiver who one day filters the topic on urgency will not see it. That is a
real limit on what this proves, and it is the right trade.

The reason it is built in `notify.py` beside the others rather than in the
command is that a path proven with a message shaped differently from a real
alert has not been proven.

## 33. Amazon Bedrock is a nicety, and the first live account proved it

The first run against real credentials came back:

    bedrock  not used: ValidationException: Operation not allowed
    sns      sent

The keys were right, the region was right, the topic was right, and the model
id was right. Amazon Bedrock inference quotas are applied at zero on accounts
without billing history, and this one had just come back from suspension. The
quota is marked not adjustable, so there is no self service path. It needs an
account verification case.

Nothing about the household's safety depended on the outcome. The judgement
was made by the engine, the sentence was the engine's own, the message went
out through SNS, and the one line in the terminal said which part had been
skipped and why.

*Consequence:* this is the design working, not the design failing, and the
write up says so in those words. Bedrock chooses how a message reads. It never
decides whether one is sent, and it is never on the path between seeing a
silence and telling somebody about it. A product that phones a family when
their mother has not moved cannot have a language model in that path.
