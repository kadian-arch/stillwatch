# Security model

Stillwatch holds a record of when one person is at home, when they sleep, and
when their house is empty. That record is valuable to the family it was built
for and valuable to anybody else, which makes confidentiality a safety
property here rather than a courtesy. The controls below are written down so
that a household, or anyone reviewing this, can see what is protected, how,
and what is not.

Nothing in this document is a secret. Keys, codes and addresses live in the
environment of the deployment and appear nowhere in this repository.

---

## What is worth protecting

| Asset | Why it matters |
|---|---|
| The occupancy record | Shows when the house is empty and when the household sleeps |
| The answer endpoint | An answer stops the service chasing a stretch of quiet |
| The event webhook | Fabricated motion would make an empty house look occupied |
| The health endpoint | It is open, so it says only whether the service is alive, never when anybody last moved |
| Ring credentials | Access tokens and the webhook signing key |
| Caregiver contact details | Held only when SMTP is used instead of SNS |

Stillwatch never requests, receives or stores video or images. The Ring scopes
it asks for are read only. There is no footage to lose.

## Trust boundaries

Three untrusted inputs reach the service: a browser, Ring's webhook, and the
environment it is deployed into. Everything that crosses the first two is
authenticated before it is acted on. The third is trusted, and is the reason
secrets live in configuration rather than in code.

---

## Controls

### Authentication and session management

Access is granted either to **named people, each holding a code of their own**
(`STILLWATCH_MEMBERS`), or to **one shared household code**
(`STILLWATCH_PASSCODE`). Named people are preferred, because an answer then
carries the name of a particular person rather than of whoever holds the
family's one secret.

Sessions are **stateless bearer tokens**: a payload carrying the name and an
expiry, and an **HMAC-SHA256** tag over it. The tag is verified with
`hmac.compare_digest`, so a forged or altered token is rejected, and the expiry
cannot be extended because it sits inside the signed region.

The signing key is `STILLWATCH_SESSION_SECRET`. Where that is absent, a key is
**derived** from the webhook secret through SHA-256 with a domain separation
prefix, so that material issued for one purpose can never be presented as
material for another.

Cookies are set `HttpOnly` (unreadable by page script, which contains the blast
radius of any cross-site scripting), `SameSite=Lax` (cross-site requests carry
no session, which removes cross-site request forgery as a route to the answer
endpoint), and `Secure` over HTTPS.

Holding no session table means there is no store to read, nothing to grow
without bound, and nothing lost when the container restarts.

### Credential handling

Codes are compared in **constant time**, so the time a refusal takes reveals
nothing about how much of the code was right. A name that belongs to nobody is
compared against a decoy of the same shape, so refusals take the same time
whether or not that person exists.

A failed sign in returns one message for every kind of failure. It never says
whether the name or the code was the problem.

### Rate limiting

Sign in is limited to **eight attempts per caller per fifteen minutes**, and a
correct code offered while limited is refused like any other, so the limit
cannot be stepped around by guessing until it lifts and then presenting the
answer. Answering is limited to thirty a minute. The caller is taken from the
first entry of `X-Forwarded-For`, which is used only for counting and never
for a decision about trust.

### Webhook integrity

Every delivery to `/ring/events` is verified against an **HMAC-SHA256**
signature using the key Ring issued, before the body is parsed. An unsigned or
wrongly signed delivery is refused with `401` and nothing is read from it.

Writes are **idempotent on the event id**, and ids are derived from the content
of the event, so a redelivered or replayed event changes nothing. This matters
twice over: Ring redelivers anything it is not certain arrived, and a
duplicated motion event would shorten a silence that never actually broke.

### Browser surface

A content security policy allows script only from this origin, style from this
origin and Google Fonts, and no inline script at all. `frame-ancestors 'none'`
and `X-Frame-Options: DENY` prevent the page being framed, which rules out
clickjacking of the answer buttons. `X-Content-Type-Options: nosniff`,
`Referrer-Policy: same-origin`, and a permissions policy disabling camera,
microphone and location are set on every response, as is
`Strict-Transport-Security` for a year including subdomains, so that not even
the first request of a session can be attempted in clear. API responses are
`Cache-Control: no-store`.

Every piece of text on the page is written as text content, never as markup, so
a camera named by somebody else cannot carry script into the page.

### Accountability

Every answer is stored with the name of whoever gave it and the moment it was
given, and that name is sent to everybody else on the notification list at the
same time. Nothing is ever deleted: the reading that caused an answer stays on
the page underneath it. An answer the family does not recognise therefore
arrives in their inbox with an unfamiliar name on it rather than passing
unnoticed.

### Availability

The failure that costs the most here is silent: a service that has stopped
judging looks exactly like a household where nothing is wrong. `stillwatch
watchdog` runs on a schedule, reads when the judge last ran and when anything
last arrived from the cameras, and raises it if either has gone stale. It
repeats a standing fault a few times a day on a long gap rather than on every
run, because an alarm that repeats constantly is one that gets filtered.

### Data minimisation

Over Amazon SNS, caregivers subscribe themselves to a topic and Stillwatch
never holds an address or a phone number. SMTP is the fallback and does hold
them, in configuration, which is the weaker of the two and the reason SNS is
recommended.

---

## Residual risk

**A shared household code identifies a household, not a person.** Where
`STILLWATCH_MEMBERS` is used this does not apply, and each answer carries a
particular name. Where the single shared code is used, an answer is
attributable only to whoever holds it. Neither arrangement is multi factor,
and neither would be enough for a care service answering for many homes; that
would want an identity provider behind it, which this deliberately does not
carry.

**Rate limits are per process.** One container is the deployment shape, and a
second would keep its own counters, loosening the limit without removing it. A
shared counter is the fix if it ever runs wider.

**The webhook signing key cannot be rotated from here.** It is issued once, so
replacing it means issuing a new one at the source.

**Running without a code is permitted, and announced.** A deployment with
neither setting serves the household to anyone who knows the address, and says
so in a panel at the top of its own page. This is deliberate: a lock nobody
knows about is worse than no lock, and a demonstration has to be reachable.
The recorded days are open in every configuration, because they are a
simulated household and exist to be looked at.

**Who reads the dashboard is not recorded.** Who answered, and when, is.

---

## Verification

The checks in `stillwatch/tests/test_service.py` exercise this directly: that
the household and the answer endpoint refuse an unauthenticated
request, that the recorded days do not, that a token signed with the wrong key
is rejected, that a token with no valid tag is rejected, that repeated wrong
codes are throttled and a correct code offered while throttled is still
refused, that an answer carries the name it was given under, that the response headers
above are present, and that the open health endpoint never reports when
anybody last moved.

Run them with `python check.py`.
