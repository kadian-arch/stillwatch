# Ring developer friction log

Written as each problem happened rather than reconstructed afterwards, while
building Stillwatch on the Ring Partner API for the Amazon Build, Ship, Shape
hackathon.

Each entry states the task, what was expected, what actually happened, how
severe it was, what we did instead, and what would have helped.

Severity is judged by what it cost a solo developer with a month:
**blocking** stopped work entirely, **high** cost a day or more or forced an
architectural decision, **medium** cost hours, **low** was an annoyance.

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
