# Build journal

Kept as we go. Three things are drawn from it later: the Devpost story, the
developer friction log, and the product feedback. Written once, used three
times.

Entries are dated. Frictions are recorded when they happen, with what was
tried, because a friction log written from memory in October will be vague and
worth nothing.

---

## Sun 20 Sep 2026

### Decided

**Stack.** Python and Flask, SQLite for events, plain HTML and CSS and JS for
the dashboard with no build step. Deployment target is Heroku, using the GitHub
Student Pack allowance, because the Ring webhook needs a public HTTPS endpoint
and Heroku provides a valid certificate without any certificate work.

**Simulator first.** The whole engine is developed against generated events. No
part of the build waits on Ring access.

**No neural network.** Frequency baselines and gap percentiles instead. In a
caretaking product a caregiver has to be able to read why their phone went off
at three in the morning, so explainability is a product requirement rather than
a preference. Recorded here because the reasoning belongs in the submission.

### Built

`ring-event-simulator`, standalone and MIT licensed. Six devices, six
scenarios, forty passing checks.

The design decision that made it work: generate where the person is, not what
the sensors saw. Events fall out of occupancy. An absence then needs no special
code at all, because while the zone is `out` there is genuinely nobody for an
interior camera to see.

Cameras carry a cooldown, because a real Ring camera does not emit an event per
second of continuous movement. Leaving that out would have produced an
unrealistically dense stream and skewed every gap statistic we derive, and the
error would only have surfaced against real Ring data, far too late.

---

## Mon 21 Sep 2026

### Built

The rhythm learner, in a `stillwatch` package that does not import the
simulator. Events enter through one normalise function and devices through one
roster loader, so swapping in the Ring API touches those two places only.

### Decided

**Tolerated quiet is the longest daily silence, not the average gap.** With
four interior cameras the average gap between events is a couple of minutes and
the ninety fifth percentile is around a quarter of an hour, which would fire ten
times a day. The question the monitor actually asks is how long the house has
been silent, so the baseline has to describe the longest stretch this person
normally produces: one sample per day per hour, not one per gap.

**Silence is keyed by the hour it began, not the hour it is noticed.** A silence
that starts at eleven at night is still a night silence at three in the morning,
and looking it up under three would compare it against a waking hour.

**Departures are excluded from the baseline.** A silence that opens within half
an hour of a door event is a trip out. Letting those in would raise the
threshold until a real collapse fell underneath it. The test suite learns the
same history twice, with the rule and without, and shows the daytime thresholds
move, so the rule is proven rather than asserted.

**Offline devices are dropped from the denominator.** A camera that reported
itself down must not drag its own learned rhythm towards zero, and its silence
must never read as a person who stopped moving.

The result: daytime tolerance of roughly half an hour, night tolerance of five
to nine hours, with neither figure written down anywhere. Both are learned from
the household.

### Built later the same day

The silence monitor and the escalation ladder. Running every scenario against
its own declared expectation drove five product decisions that would not have
come out of thinking about it.

**Being out longer than usual is shown, not shouted.** The first version
escalated a nine hour outing to concern because she is normally back within
three. That is the false alarm the product exists to avoid: somebody spending
the day at their sister's must not set off a phone. An unusual absence is now
flagged on the dashboard and stays off the ladder, and only a full day with no
return escalates.

**A missed habit lifts concern to alert, but never lifts quiet to concern.**
Someone sleeping in is late for every habit they have and perfectly fine. The
escalation only applies once the silence is already well past normal.

**A dead camera cannot miss a habit.** An anchor on a camera that was offline
during its window is skipped. Without this, a broken kitchen camera produced
"she is normally in the kitchen by 08:48 and has not been", which is true and
completely meaningless.

**A dead camera in a room she normally uses holds the alarm back to concern,
and names the camera.** If we cannot see the room she would be in, that is a
likelier explanation than a collapse, and over-claiming trains the caregiver to
ignore us. The first version held back on *any* offline camera, which meant one
flaky camera anywhere could switch off alerting for the whole house. Now it only
applies to a camera she is normally active in front of at that hour.

**A caller is not somebody getting up.** Only a door event at the start of a
silence explains it. A doorbell press three hours into a collapse leaves the
alert exactly where it was, and there is a test that injects one to prove it.

170 checks across three suites.

### Dashboard

Flask service and a plain HTML, CSS and JS page with no build step. The whole
day is computed once on the server and scrubbed in the browser, so replay is
smooth enough to record the demo from directly.

**Habits are onsets.** Building the page exposed that the learner had invented
habits at window boundaries: sitting in the living room across eleven o'clock
produced "she arrives at 11:04, give or take three minutes". A habit is now the
first movement after at least half an hour of stillness on that camera, so only
things that genuinely start at a time count.

**A habit on a dead camera reads as camera off, never as not yet.** The page
must not suggest she skipped breakfast when the truth is that we cannot see
the kitchen.

**Times are the household's clock.** Rendering timestamps in the viewer's time
zone would put a caregiver abroad an hour or more out from what the house saw.

198 checks across four suites.

---

## Tue 22 Sep 2026

### Dashboard redesign

The first version shouted everything at the same volume, including all normal:
a green tinted card, filled pills, saturated blocks. The rule now is that
normal is visually quiet and only deviation draws the eye. Normal is a dim wash
that recedes; alert is the only strong colour on the page. The layout was
rebuilt for three widths, with the sentence and the gauge above the fold on a
laptop.

It also exposed a bug: a silence that began before midnight showed as "since
00:00".

### Notifications

**A missed habit only counts if it fell due during the silence.** Checking every
five minutes instead of every fifteen found a false alert on the lie in day: she
missed her morning habit by sleeping in, was busy all afternoon, sat still after
lunch, and the monitor treated the morning as evidence about the afternoon. The
monitor tests now check every five minutes, as the service does, because a false
alarm lasting five minutes still wakes someone.

**A low urgency message waits fifteen minutes; an urgent one never waits.**
Without the hold, the lie in day would still have texted the family about a
forty five minute sit after lunch.

**Only a channel that reaches a person counts as delivery.** If SNS fails and
the log file succeeds, the message has not been sent, and it is retried.

**The browser script is syntax checked by the test runner.** A broken string
reached the page while every test passed, because the tests only proved the
script was served.

242 checks across five suites, plus the script check.

---

## Tue 22 Sep 2026, later

### Ring connection

Built against the published Partner API shapes: `POST https://oauth.ring.com/oauth/token`
with scope `ava.v1:read`, history at `/v1/history/devices/{id}/events`, and webhooks
signed with HMAC-SHA256 in an `X-Signature` header, hex, prefixed `sha256=`.

**Ring's vocabulary is translated in one file.** Ring says `motion_detected`
where the engine says `motion`, and wraps payloads the way JSON:API does.
Everything downstream works on our own event type, so the same engine runs
against the simulator and against real hardware.

**Several spellings of each field are accepted.** The docs leave some shapes
open, and three in the morning is a poor time to discover that a key was named
differently.

**Writes are idempotent on the event id.** Ring redelivers a webhook it is not
sure we received, and a duplicated motion event would shorten a silence that
never actually broke. An event that arrives without an id gets a deterministic
one derived from device, type and time, so a redelivery still collides.

**An unreadable payload is answered 200, not 400.** Ring cannot fix a payload we
cannot parse by sending it again, and a retry loop helps nobody. A bad signature
is a different matter and gets 401.

**A camera with no recognisable name is treated as inside the home.** Mistaking
a door for a room only costs an explanation. Mistaking a room for a door would
let a real silence be explained away.

### Live mode

The dashboard now has a source that reads the store the webhook writes to, so
it shows today rather than a recording, stopping at the present instead of
midnight and refreshing every minute. A replayed day is cached; a live one never
is, because today keeps happening.

327 checks across seven suites.

---

## Wed 23 Sep 2026

### History backfill

The moment an account is linked, Stillwatch asks Ring for everything it already
holds, so the first useful judgement comes on day one rather than in November.
A baseline needs weeks of history and nobody will wait weeks after installing.

**One failing camera must not cost the others.** A household where the porch
camera errors should still learn its kitchen. Each device is fetched
independently and failures are collected into the report rather than raised.

**A pagination link is data, not an instruction.** Ring does not document how
history pages, so the client follows the JSON:API `next` convention when it is
offered, and refuses any link pointing at a host other than Ring's own API.
Following one blindly would send our access token wherever the reply said.
There is a test that serves a link to another host and proves the paging stops
instead of fetching it.

**Reading history twice stores nothing twice.** The store is idempotent on the
event id, so a repeated backfill is safe, which matters because it will be run
again whenever we suspect a gap.

389 checks across seven suites.

---

The Ring developer friction log lives in [FRICTION_LOG.md](FRICTION_LOG.md).
