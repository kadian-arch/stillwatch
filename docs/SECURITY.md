# What Stillwatch is worth attacking, and what stops it

Stillwatch knows when somebody is at home, when they are asleep, and when the
house is empty. That is useful to a family and valuable to a burglar, and the
dashboard would publish all of it to anyone who guessed the address. The
answer buttons are worse than the reading: whoever can press one can silence
an alarm about a real person.

This is what was done about it, written from the attacker's side, because that
is the only side that finds anything.

---

## 1. Read the household without being invited

**The attack.** Open the address and read the page. Learn the hours the house
is empty, which door they leave by, and that nobody has been there since
Friday.

**What stops it.** A household passcode. Set `STILLWATCH_PASSCODE` and the
live household and every answer route need a signed session; without one they
answer `401`. The recorded demonstration days stay open, because nothing in
them came from a real home.

Leave the passcode unset and the service runs open, and *says so on the page*
in the same panel a judge or a visitor would read first. A lock nobody knows
about is worse than no lock, so the open state is stated rather than implied.

**What is left.** The passcode is one secret for the whole household rather
than an account each. For a family of four sharing a dashboard that is the
right trade; for a care service running many homes it is not, and that is the
first thing that would need replacing.

## 2. Silence a real alarm

**The attack.** `POST /api/answer` with somebody else's episode. The chasing
stops, the family stop being reminded, and nobody goes round.

**What stops it.** Answering requires a session, is rate limited to thirty a
minute per caller, and every answer is stored with the name of whoever gave
it. That name is sent to everybody else on the notification list the moment it
is pressed, so an answer nobody in the family recognises arrives in their inbox
with a stranger's name on it rather than disappearing quietly.

An answer never deletes anything. The reading that caused it stays on the page
underneath, and the engine's own judgement is unchanged.

## 3. Guess the passcode

**The attack.** Sit on `POST /api/session` and work through a word list.

**What stops it.** Eight attempts per caller per fifteen minutes, counted on
the forwarded address, and a correct passcode offered while throttled is
refused with the same `429` as a wrong one. The comparison itself is constant
time, so the answer cannot be measured a character at a time.

**What is left.** The counter lives in the process. One dyno is the shape this
is deployed in, and more than one would each keep a separate count, which
loosens the limit without removing it. A store backed counter is the fix if it
ever runs wider.

## 4. Forge a session

**The attack.** Write a cookie that claims to be the daughter.

**What stops it.** The cookie is a payload and an HMAC-SHA256 of it. The key
is `STILLWATCH_SESSION_SECRET`, or a key *derived* from the webhook secret so
that a session cookie can never be replayed as something Ring signed. Expiry
is inside the signed payload, so it cannot be extended by editing it. The
cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` over HTTPS.

There is no session table, which means there is nothing to read, nothing to
grow, and nothing lost when the dyno restarts.

## 5. Feed Stillwatch events it did not earn

**The attack.** Post invented motion to `/ring/events` to make a house look
alive while nobody is in it.

**What stops it.** Every delivery is checked against an HMAC-SHA256 signature
with the key Ring issued, before the payload is parsed. Writes are idempotent
on the event id, so a replayed delivery changes nothing.

**What is left.** The signing key cannot be rotated from the dashboard. Ring
shows it once at creation, so a leak means creating a new application. That is
Ring's design, and it is in the friction log.

## 6. Make the page do something on a reader's behalf

**The attack.** Frame the dashboard, or get a script onto it.

**What stops it.** `X-Frame-Options: DENY` and `frame-ancestors 'none'`. A
content security policy that allows scripts only from this origin, styles only
from here and Google Fonts, and no inline script at all. `nosniff`,
`same-origin` referrers, and camera, microphone and location switched off by
policy. Every API response is `no-store`.

Nothing on the page is built from a string: every piece of text is set as text
content, never as markup, so a device named by somebody else cannot carry a
script into the page.

## 7. Watch the watcher die

**The attack.** None needed. This one happens on its own, and it is the
failure that costs the most: a judge that has stopped running and a household
where nothing is wrong look exactly the same from outside.

**What stops it.** The watcher writes the time of every judgement to the
store. `stillwatch watchdog` reads that and the time of the last delivery, and
sends an urgent message if either has gone stale. It says each fault once a
day, because an alarm that repeats is an alarm that gets filtered.

---

## What is deliberately not protected

**The recorded days are public.** They are a simulated household and exist to
be looked at.

**There is no audit log beyond the answers table.** Who looked at the
dashboard is not recorded. Who answered, and when, is.

**Stillwatch holds no video and no images.** It reads only that movement was
seen and at what time, so there is nothing of that kind to steal. The Ring
scopes it asks for are read only.

**Contact details.** Over SNS, Stillwatch never holds them: a caregiver
subscribes themselves to the topic. Over SMTP it holds the addresses in an
environment variable, which is the weaker of the two and the reason SNS is the
recommended channel.
