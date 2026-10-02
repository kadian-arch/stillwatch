# Product feedback

Feedback on the Amazon tools, APIs and SDKs used to build Stillwatch, an
inactivity monitor for somebody living alone.

Where something got in the way, the detail is in
[FRICTION_LOG.md](FRICTION_LOG.md) with the date it happened. This document is
the considered view: what worked, what did not, and what would change it.

One thing is stated plainly up front, because it shapes everything below.
**Ring hardware was not obtainable where this was built, and the AWS account
was suspended during the build.** Every judgement here is honest about which
parts ran against the live service and which did not.

---

## Ring Partner API

**Used for:** the OAuth account-linking flow, `GET /v1/devices`, the device
history endpoint, and the signed event webhook. Verified against
`api.amazonvision.com` with a Developers Playground token.

### What is good

**The webhook is signed, and signed properly.** HMAC-SHA256 over the raw body,
with the digest in `X-Signature`. This is the right design and it is not
universal among partner APIs. It meant we could accept deliveries from anywhere
on the public internet and verify them with a constant-time comparison, and it
let us build an event feeder of our own that exercises the real production
path rather than a stubbed one.

**The envelope is consistent.** Everything comes back as JSON:API with `data`,
`attributes` and `relationships`, so one unwrapping function handled devices,
history pages and webhook deliveries alike. Related resources are reachable by
the `links.related` path rather than by string assembly.

**Requesting narrow scopes is possible and respected.** Stillwatch asks for
motion and doorbell events only. It never requests video, and the platform does
not force it to. For a product whose entire promise is "nobody has to be watched
to know they are alright", that mattered.

**The Developers Playground exists.** Being able to get a token and call the
real API without owning hardware is the single most useful thing on the
platform for a developer in our position, and it let us prove our client works
against Ring rather than against our own fixtures.

### What got in the way

**A device has no type.** Neither `/v1/devices` nor its `capabilities`
relationship says whether a device is a doorbell, a camera or a chime. For the
sandbox device the only trace is the string `DoorbellPro` inside an image URL.
Stillwatch exists to tell "they went out" from "they stopped", and that rests
entirely on knowing which cameras watch a way in or out. We classify on the
device's name, default to indoor because that is the safe direction, and
promote a device when it actually registers a doorbell press. A household that
named its cameras "Camera 1" and "Camera 2" cannot be classified at all.

**The sandbox cannot deliver an event.** The Playground simulates a live view
session with the WHEP and SDP exchange. It does not fire a motion or doorbell
event at a registered webhook, which is the only part of the API this product
consumes. The most important integration path is therefore the one a developer
cannot exercise.

**Staging assumes hardware.** Account linking authorises up to ten real Ring
accounts. A real account with no devices yields no devices and no events, so a
developer in a country where Ring is not sold reaches the end of the onboarding
path and still has nothing to build against.

**History pagination is undocumented.** We followed the JSON:API `next`
convention and refused any link pointing at a host other than Ring's own, since
following one blindly would send an access token wherever the reply named. That
is a guess we had to make and defend rather than a documented contract.

**Credentials cannot be rotated.** Client ID, client secret and signing key
appear once at app creation and can never be retrieved. The documented remedy
for a leaked secret is to delete the app, which is not a workable answer for a
live integration.

### What would change the most

1. A `type` or `family` field on a device, or a doorbell entry in capabilities.
2. A button in the Playground that fires a motion event at a webhook URL.
3. One synthetic household on the sandbox account, with devices that emit
   events on a schedule.
4. Rotatable secrets, and a permanently visible client ID.

---

## Ring Developer Console

**Used for:** registration, identity verification, app creation and account
linking.

Identity verification gates the entire platform and gives no reason when it
fails. The rule that matters, that the legal name on the Company Profile must
match the identity document exactly, is documented on a different page from the
upload screen. One of three attempts was spent discovering it.

Account linking requires four HTTPS URLs that the partner hosts, and rejects
`localhost`. The practical consequence is that a developer must deploy a public
service before they can write a line of working integration code. We built the
entire engine against a simulator first, which turned out well, but it was
forced rather than chosen.

**What would change the most:** state the name-matching rule on the upload
screen, say which check failed, and accept a `localhost` callback for an app
that has not been submitted for certification.

---

## Amazon Bedrock

**Used for:** rewriting the opening sentence of a notification so it reads like
a person rather than a system. Integrated through `boto3` against the
`converse` API, with a system prompt, a tone per notice kind, and a validator
that rejects any reply containing a number not present in the supplied facts.

**Run against the live service.** The opening sentences in production are
written by `amazon.nova-lite-v1:0`.

**What is good.** `converse` is a clean shape to code against: one call, a
messages list, a system block, and an inference configuration, with the same
signature across models. Not having to learn a different request body per model
family is a real saving for a small project. Swapping models is one environment
variable. First call to last worked out at 378ms.

**What got in the way: one error sentence for four unrelated faults.**
`ValidationException: Operation not allowed` is returned for an account that
may not call the model, a region that does not carry it, terms that were never
accepted, and a model name the account cannot use. It names neither the model,
nor the region, nor which of the four it is. Getting from that sentence to a
working call took the best part of a day and four wrong diagnoses, every one of
them consistent with the evidence available at the time.

**What made it worse: the diagnostic API contradicted the runtime.** In
`us-east-1`, on the account in question:

    aws bedrock get-foundation-model-availability \
      --region us-east-1 --model-id amazon.nova-lite-v1:0

    "authorizationStatus": "AUTHORIZED",
    "entitlementAvailability": "AVAILABLE",
    "regionAvailability": "AVAILABLE",
    "agreementAvailability": { "status": "AVAILABLE" }

and then, in the same region, same account, same model, as an account
administrator in CloudShell:

    aws bedrock-runtime converse --region us-east-1 \
      --model-id amazon.nova-lite-v1:0 ...

    ValidationException: Operation not allowed

The same call in `us-west-2` answered normally. So the restriction is per
account and per region, and the API whose entire purpose is to report whether a
model may be used does not report it. Service Quotas did not show it either:
the Nova Lite on-demand limits read 100 requests and 4,000,000 tokens a minute,
not zero. Having checked both of the places AWS provides for checking, a
developer still has no way to learn this except by calling the model in every
region in turn.

Two changes would have saved the day. First, distinct error codes, or at
minimum the region and the failing precondition in the message text. Second,
`GetFoundationModelAvailability` reporting account and region eligibility,
since a model reported AVAILABLE and AUTHORIZED in a region that will not serve
it is worse than no answer, because it is believed.

**A smaller one: a model has two names.** `amazon.nova-lite-v1:0` and the
cross region inference profile `us.amazon.nova-lite-v1:0` are the same model,
and which one an account may use varies. Some models refuse the plain id and
say so only in the error text at the moment of the call. The model card could
carry both and say when each applies.

**And a stale trail.** The model access page has been retired in favour of
models enabling themselves on first invocation, which is a genuine improvement,
but a good deal of current writing still directs you to that page. Arriving at
a page that says it no longer does anything, while following an instruction to
use it, is an unhelpful first minute.


**What we would ask for.** A documented way to express "do not introduce any
token that is not in the input" would let a caller state the constraint rather
than validate afterwards. We wrote a checker that rejects invented numbers,
banned words, excess length and silence, and falls back cleanly, which is the
right belt-and-braces design for a safety product, but the first line of
defence currently has to live in the caller.

---

## Amazon SNS

**Used for:** delivering notifications to caregivers. A topic is published to,
and caregivers subscribe themselves by email or text, so Stillwatch never holds
anybody's contact details. That property is why SNS was chosen over sending
mail directly.

**Not yet run against the live service,** for the same reason. The channel is
tested against a stand-in and an email channel is used in the meantime.

**What is good as a design fit.** Publish-and-subscribe is exactly right for
this product. A household can add a second daughter without the application
storing a single new personal detail, and unsubscribing is the subscriber's
own to do. Message attributes let urgency ride along with the message so a
subscriber can filter.

---

## AWS account onboarding

This is the one that cost the most, and it is worth saying because it affects
who can build on AWS at all.

A new account was created and verified, then suspended, because the payment
method was a virtual card issued through a mobile money service. AWS does not
accept virtual cards. That is a reasonable policy and it is documented. What
was not reasonable was the sequence: the account activated, credits were
redeemed against it, and only afterwards was the payment method rejected, at
which point every AWS service became unavailable at once, including the ones
the credits were granted for.

Support handled it well once a case was open. The reply was prompt, explained
the policy clearly, named what would be accepted, and explicitly said there was
no time pressure. That was genuinely good service.

**What would change the most.** Validate the payment instrument at the moment
it is added, not after activation. In much of Africa a virtual card on mobile
money is the ordinary way to pay online, and a bank-issued card is not
something a student can produce the same week. Telling somebody at sign-up that
their card will not work is a minor inconvenience. Telling them after they have
built on the account is the difference between shipping and not.

---

## Summary

The Ring Partner API is well built where it counts: signed webhooks, a
consistent envelope, narrow scopes that are actually respected. Its gaps are
all in the same place, which is the path a developer without hardware has to
walk. A device type field and a sandbox that can fire an event would between
them remove most of what is in the friction log.

Bedrock and SNS were both straightforward to build against and well suited to
this product. Neither has yet run against the live service, and that is an
account problem rather than an API one.
