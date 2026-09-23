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
