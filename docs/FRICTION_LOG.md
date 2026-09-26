# Ring developer friction log

Written as each problem happened rather than reconstructed afterwards, while
building Stillwatch on the Ring Partner API for the Amazon Build, Ship, Shape
hackathon.

Each entry states the task, what was expected, what actually happened, how
severe it was, what we did instead, and what would have helped.

Severity is judged by what it cost a solo developer with a month:
**blocking** stopped work entirely, **high** cost a day or more or forced an
architectural decision, **medium** cost hours, **low** was an annoyance.

## The three that would change the most

1. **Give a device a type.** Nothing in `/v1/devices` or its capabilities says
   whether a device is a doorbell or a camera, and telling those apart is the
   entire basis of this product. Entry 8.
2. **Let the sandbox fire an event at a webhook.** The Playground can start a
   video session but cannot deliver the motion event an integration is built
   around. Entry 10.
3. **Let credentials be rotated.** They are shown once, cannot be recovered,
   and the documented remedy for a leaked secret is to delete the app. Entry 9.

Of eleven entries: one blocking, five high, three medium, two low. Every one
of them was written the day it happened.

---

## 1. Identity verification gates the whole platform, and says nothing when it fails

**Task.** Register as a Ring developer to obtain API credentials.

**Expected.** Sign up, verify, build.

**What happened.** Registration requires government photo ID before any API
access at all, with a limited number of attempts. A national identity card was
rejected with no indication of what had failed: not whether the image was
unreadable, the document type unsupported, or the name mismatched. Only later,
in a different page of the documentation, did we find the actual rule:

> The full legal name on your Developer Console Company Profile must match the
> name on your ID exactly.

The profile name had not been set to match. One of three attempts was spent
discovering a requirement that was never shown at the point of upload.

**Severity.** Blocking. Several days passed waiting for a passport, with no way
to touch the API in the meantime.

**Workaround.** Build the entire engine against a simulator we wrote ourselves,
so that nothing downstream waited on Ring access. That turned out well, but it
was forced rather than chosen.

**What would have helped.**
1. State the name-matching rule on the upload screen, not in a separate guide.
2. Say which check failed. "Name does not match your profile" would have cost
   one minute instead of several days.
3. Do not count a rejection caused by image quality against the attempt limit.

---

## 2. Account linking cannot be completed without a deployed, public HTTPS service

**Task.** Connect a Ring account to a private app in order to receive events.

**Expected.** Local development against a test account, as with most APIs, with
a `localhost` callback during development.

**What happened.** Account linking requires four URLs, all of which the partner
must host, and all of which must be HTTPS: Account Link URL, Default Redirect
URL, Token Exchange URL and Webhook URL. `localhost` is not accepted.

The practical effect is that a developer cannot write a single line of working
integration code until they have chosen a host, paid for it, deployed, and
obtained a certificate. For a student on a hackathon deadline that reverses the
natural order of work: hosting decisions, which normally come last, have to be
made before anything can be tested.

**Severity.** High. It forced a hosting choice on day one and made deployment a
prerequisite for development rather than a result of it.

**Workaround.** Build and test every endpoint locally against a test client,
including HMAC signature verification, so that deployment is the only unknown
left when hosting becomes available.

**What would have helped.** Allow `http://localhost` callbacks for private apps,
or provide a first-party tunnel, the way several other event platforms do.
Private apps skip Appstore certification already, so the case for requiring
public HTTPS during development is weaker there.

---

## 3. The direction of the OAuth flow is not stated plainly

**Task.** Work out who calls whom during account linking.

**Expected.** One page saying: the partner does this, Ring does that.

**What happened.** The Get Started page says the developer obtains credentials
and calls Ring's token endpoint at `oauth.ring.com`. The Configure page says
Ring sends an authorisation code to a Token Exchange URL that the partner
hosts. Both are true, but read separately they suggest opposite designs: in one
the partner is the OAuth client, in the other the partner looks like an OAuth
provider. Reconciling them took three pages and a judgement call.

**Severity.** Medium. Hours lost, and worse, it is the kind of ambiguity that
produces code which appears correct until a real account is linked.

**Workaround.** Accept the authorisation code from either a JSON body or a form
post, and exchange it at `oauth.ring.com` regardless of which side initiated.

**What would have helped.** A single sequence diagram with the partner and Ring
as the two participants, showing each request and who makes it.

---

## 4. There is no official SDK, so every developer writes the same client

**Task.** Call the API.

**What happened.** Everything is plain HTTP: the OAuth exchange, the token
refresh, the event history call, the webhook signature check. All of it is
straightforward, and all of it is the same work for every developer who ever
integrates, including the same mistakes.

**Severity.** Medium. A day of work that produced nothing unique to the product.

**Workaround.** A small client of our own, roughly two hundred lines, with the
transport injectable so it can be tested without network access.

**What would have helped.** A reference client in one or two languages, even
explicitly unsupported. Alternatively, publish an OpenAPI description and let
developers generate their own.

---

## 5. Webhook payload shapes are left open

**Task.** Parse an incoming event.

**What happened.** The documentation names the event types precisely
(`motion_detected`, `button_press`, `device_online`, `device_offline`) and
specifies the signature exactly (`X-Signature`, HMAC-SHA256, hex, prefixed
`sha256=`), which was genuinely useful. What it does not pin down is the
envelope: whether an event arrives bare or wrapped as JSON:API `data` and
`attributes`, whether the device is a field or a relationship, and whether an
event id is always present.

**Severity.** Low to medium. Recoverable, but it makes defensive parsing
mandatory, which is code nobody enjoys writing or reading.

**Workaround.** Accept several spellings for each field, unwrap the JSON:API
envelope when present, and derive a deterministic event id from device, type
and timestamp when none is supplied, so that a redelivered webhook collides
with the original instead of being counted twice. A duplicated motion event
would shorten a silence that never actually broke, which in this product means
a missed alert.

**What would have helped.** One complete example payload per event type, copied
verbatim from what the platform actually sends.

---

## 6. History pagination is not documented at all

**Task.** Read a household's past events, so that a baseline exists on the day
the product is installed rather than a month later.

**Expected.** The history endpoint documented with its query parameters and its
pagination scheme, since history is by definition larger than one response.

**What happened.** `GET /v1/history/devices/{device_id}/events` is listed with a
one line description and nothing else. No date range parameters, no limit, no
cursor, no example of a paginated reply, no statement of the maximum page size.
The device list endpoint by contrast does document its `?include=` options,
which makes the omission look accidental rather than deliberate.

The consequence is worse than inconvenience. Without knowing the pagination
scheme, an integrator cannot tell the difference between "this household has
forty events" and "this household has four thousand events and I read the first
page". For a product whose entire job is judging how much activity is normal,
silently reading one page of history would produce a baseline that is wrong in
the dangerous direction: too little history looks like a quiet person, and a
quiet baseline hides a real emergency.

**Severity.** High. It is the difference between a correct baseline and a
confidently wrong one.

**Workaround.** Follow the JSON:API `links.next` convention when a reply offers
it, stop cleanly when it does not, refuse to follow a link pointing at any host
other than Ring's own API, and cap the number of pages so a malformed or
looping reply cannot run forever. Report how many days of history were actually
retrieved, so a human can see whether it looks plausible.

**What would have helped.** One paragraph and one example response. State the
page size, the parameter names for a date range, and whether pagination is by
cursor or by link.

---

## 7. Device requirements read as contradictory for a developer with no hardware

**Task.** Decide whether the project was possible without owning a Ring device.

**What happened.** One page implies physical hardware is needed to develop and
test. Another describes a sandbox with synthetic devices and events, and the
release notes describe a Playground that needs no app, no account linking and
no active subscription. The answer is that hardware is not required, which is
excellent news, but it took a while to be certain of it.

**Severity.** Low. Time spent reading rather than building, and a period of
genuine doubt about whether to attempt the track at all.

**What would have helped.** A line on the Get Started page saying plainly what
can be done with no hardware. For developers in countries where Ring devices
are not sold, that one sentence is the difference between building on the
platform and walking away from it.

---

## 8. Nothing in the API says what kind of device a device is

**Task.** Tell a doorbell from an indoor camera, so the product can work out
whether somebody left the house or stopped moving inside it. This distinction
is the whole product.

**Expected.** A type, family, model or category field on the device.

**What happened.** `GET /v1/devices` returns, for the sandbox device, exactly
two attributes:

```json
"attributes": {
  "name": "Playground Device",
  "image_url": ".../square_device_images/DoorbellPro/rvdp_3x.png"
}
```

Following the `capabilities` relationship gives `video`, `motion_detection`,
`image_enhancements` and `audio`, plus nulls for `glass_break_detection`,
`battery_status`, `flood_detection`, `tamper_detection`, `contact_detection`,
`co_detection_listener`, `freeze_detection` and `smoke_detection`.

There is no doorbell capability. There is no button, chime or press capability.
The device is a Doorbell Pro, and **the only evidence of that anywhere in the
response is the word `DoorbellPro` inside an image filename.**

**Severity.** High. It forced a design decision on the central mechanism of the
product.

**Workaround.** Two things, neither of which we wanted to do.

First, classify on the device's *name*, looking for words like door, porch,
gate and driveway, and default to treating a camera as indoor when the name
says nothing. Indoor is the safe default: mistaking a door for a room costs an
explanation, whereas mistaking a room for a door would let a real silence be
explained away as somebody going out.

Second, correct that classification from behaviour: a device that ever
registers a doorbell press is promoted to a way in or out, and never demoted.
Somebody ringing a bell is better evidence than what the camera was named.

We did not parse the image filename. Deciding whether an elderly person has
left their home on a substring of a PNG path is not defensible.

**What would have helped.** A `type` or `family` attribute on the device, or a
doorbell entry in capabilities. A household that has renamed its cameras to
"Camera 1" and "Camera 2" is currently unclassifiable, and those are exactly
the households least likely to have configured anything carefully.

---

## 9. App credentials are shown once and cannot be recovered

**Task.** Fetch the client ID, client secret and HMAC signature key needed for
OAuth and webhook verification.

**Expected.** Credentials visible in the console, with the secret regenerable.

**What happened.** All three appear once, on the screen shown immediately after
the app is created, behind a checkbox confirming they have been saved. After
that screen they are gone. The documentation is explicit:

> These credentials are only shown once and cannot be retrieved later.

The stated remedy is to delete the app and create a new one, which is possible
only for an app not yet submitted for certification.

**Severity.** High. Not because of what it cost us in the end, but because of
how close it came. The credentials were not obviously saved anywhere, the
console offers no way to check whether they exist, and there is no partial
view, not even the client ID, to confirm you are looking at the right app. We
had searched the machine and were about to delete the app and start again when
the downloaded CSV turned up. Had it not, account linking would have had to be
configured from scratch.

**Workaround.** Download the CSV on the credentials screen before touching
anything else, and store it outside the repository.

**What would have helped.** Let the client ID stay visible; it is an identifier,
not a secret. Allow the secret and the signing key to be rotated in place, which
is standard practice and is also what a partner needs when a key is
accidentally exposed. Deleting a production app to recover from a leaked secret
is not a workable answer.

---

## 10. The Playground exercises live view, not the events an integration listens for

**Task.** Verify our client against the real API without owning hardware.

**What happened.** The Developers Playground is genuinely useful and we used
it. It issues a short-lived OAuth token, and its API explorer let us confirm
that our own client authenticates against `api.amazonvision.com`, calls
`/v1/devices`, and parses Ring's real JSON:API envelope. That is a real
verification we could not otherwise have done.

What it does not do is deliver events. Its simulation is of a live view
session, with the WHEP and SDP exchange for a video stream. We found no way to
make the sandbox deliver a motion or doorbell webhook to our endpoint, which is
the only part of the API this product actually consumes.

Its history endpoint also returned zero events for the sandbox device, so the
shape of a populated history page remains untested against anything but our own
fixtures.

**Severity.** Medium. It left the most important integration path verified only
against code we wrote ourselves.

**Workaround.** A signed event feeder of our own, posting to our real webhook
with the real HMAC key, so that everything downstream of the endpoint runs the
production path.

**What would have helped.** A button in the Playground that fires a motion
event at a registered webhook URL. For an events-driven integration that single
feature would be worth more than the whole live view simulator.

---

## 11. The staging path assumes every developer owns hardware

**Task.** Receive real events during development.

**What happened.** Account linking offers up to ten staging users, each of whom
authorises a real Ring account. A real account with no devices produces no
devices and no events, so the staging path is only open to a developer who owns
Ring hardware, or knows somebody who does and is willing to link their home.

Ring devices are not sold in much of the world. A developer in one of those
countries can register, verify their identity, create an app, read the
documentation and link an account, and still have no way to see a single event.

**Severity.** High, and structural rather than a bug.

**Workaround.** Our own simulator, published as a separate open source project,
feeding the real signed webhook.

**What would have helped.** One synthetic household on the sandbox account,
with a handful of devices that emit motion and doorbell events on a schedule.
It would cost Ring very little and it would open the platform to every
developer who cannot buy the hardware. The hackathon rules say a physical
device is not required; the staging path does not yet reflect that.
