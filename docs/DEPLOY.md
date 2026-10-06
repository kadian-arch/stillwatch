# Running Stillwatch for a home

Stillwatch needs a public HTTPS address before Ring will connect to it, because
Ring delivers events to a webhook and will not accept `localhost`. Everything
below is about getting one, and there are two ways depending on whether you
want a server.

Replace `your-domain.example` throughout with a domain you control.

## What Ring needs from you

A Ring developer app, created at the Ring developer console. Creating one issues
three values, shown **once**:

| Value | Used for |
|---|---|
| Client ID | identifying the app when asking for a token |
| Client secret | proving it is really the app |
| HMAC signature key | checking that a webhook really came from Ring |

Save all three when they appear. They cannot be shown again, and the only
remedy is to delete the app and create another.

The app then needs four addresses on your own domain, which this service
already serves:

| Field | Route |
|---|---|
| Account Link URL | `/ring/link` |
| Default Redirect URL | `/ring/linked` |
| Token Exchange URL | `/ring/token` |
| Webhook URL | `/ring/events` |

Ask only for motion events and doorbell presses. Stillwatch never requests
video and cannot use it.

Those routes are public by necessity, since Ring's servers have no way to sign
in. `/ring/events` verifies an HMAC signature on every delivery and answers 401
without one, so the signing key is what protects it.

## Option A: a small server

Suits a permanent installation. Ubuntu 24.04, any provider, roughly the
smallest paid instance.

Give the machine a fixed address, then point an `A` record for
`your-domain.example` at it.

Then, on the machine:

```bash
curl -fsSL https://raw.githubusercontent.com/kadian-arch/stillwatch/main/deploy/setup.sh -o setup.sh
sudo bash setup.sh your-domain.example
```

That installs Python, Postgres, the service and Caddy, generates a database
password, and starts everything. Caddy obtains the HTTPS certificate on its
own once the name resolves. The script asks nothing and is safe to run again,
which is also how you update after new code lands.

Settings live in `/etc/stillwatch.env`, readable only by root and never in the
repository:

```
STILLWATCH_RING_CLIENT_ID=
STILLWATCH_RING_CLIENT_SECRET=
STILLWATCH_RING_WEBHOOK_SECRET=
STILLWATCH_PERSON=
STILLWATCH_TZ=Europe/London
```

`STILLWATCH_TZ` is the home's own timezone, and it matters more than it looks.
Every threshold is learned against the hour of the household's clock, so a
home an hour from UTC with this unset would have each of them shifted by an
hour. Anyone looking at the dashboard from elsewhere is told how far the two
clocks are apart, but the times themselves always belong to the house.

Then `sudo systemctl restart stillwatch`.

If you have no domain yet, a hostname like `203-0-113-4.sslip.io` resolves to
that address without a registrar and gets a real certificate.

## Option B: no server

A tunnel gives a machine you already own a public HTTPS address without opening
a port or exposing its address. Useful for a single home, and the same four
Ring addresses work.

Run the service on the machine:

```bash
python -m stillwatch serve --live --port 8420 --person "who lives here"
```

Then point a tunnel at `http://localhost:8420` and route
`your-domain.example` to it. Cloudflare Tunnel and similar tools do this in a
few commands.

The tradeoff is uptime. When the machine is off, no webhooks arrive. Ring keeps
its own history, so `python -m stillwatch backfill --days 30` fills the gap
afterwards.

## Protecting the dashboard

The dashboard shows when a person is at home, when they sleep and when the
house is empty, and its answer buttons let whoever presses one silence a real
alarm. It should not be open to the internet.

There are two arrangements. Both need a signing key:

```
STILLWATCH_SESSION_SECRET    a long random string, used to sign session cookies
```

**A code each**, which is the one to use. One setting lists who may sign in and
what each of them types:

```
STILLWATCH_MEMBERS   Lucie:7F3K2Q-xxxx, KAD:9ZP4MX-xxxx, Nurse:4QW8TB-xxxx
```

Each person signs in with their own name and their own code. Nobody can sign
in under a name that is not on the list, and the name recorded against an
answer is always the household's spelling of it, never what was typed. Taking
somebody off the list is deleting their entry.

**One shared code**, simpler and weaker:

```
STILLWATCH_PASSCODE          something long the family can read down a phone
```

Anybody holding it signs in with a name they choose. That tells you a
household answered, not which person did.

Either way, sessions last a fortnight and are a signed cookie, so a restart
signs nobody out and there is no session table to leak. The name is stored
with anything that person answers and sent to everybody else, so the family
can see who has already been round.

Leave the passcode out and the service runs open. It will say so in a panel at
the top of its own page, which is the point: a lock nobody knows about is
worse than no lock.

The recorded demonstration days stay readable either way. Nothing in them came
from a real home.

If a proxy is wanted as well, put it in front of everything except `/ring/`,
which must stay reachable for Ring's servers. Cloudflare Access does this with
two applications: one covering `*` that allows named email addresses, and one
covering `ring/*` set to bypass. The more specific path wins.

## Keeping it honest while nobody is looking

Two scheduled jobs, every ten or fifteen minutes.

```bash
python -m stillwatch watchdog
```

Says so if the part that judges the household has stopped running, or if
nothing has arrived from the cameras for an hour. It reports each fault once a
day rather than every time it runs. Without it, a service that has quietly
died looks exactly like a house where nothing is wrong, which is the one
failure this cannot afford.

```bash
python feed.py --catch-up
```

Only for a deployment with no real cameras behind it. It works out what the
simulated household has done since the last event stored, posts it through the
real signed webhook, and stops. Safe to run as often as you like: every event
carries an id derived from itself, so anything already stored is refused.

It also posts the reports the cameras would have made through the stretch it
is filling, every half hour. Motion on its own does not repair a gap: a day
with movement in it but no sign of the cameras checking in still reads as a
day nothing reported, because that is exactly what it looked like at the time.

To repair a stretch that was missed entirely, name the day to start from, as
`YYYY-MM-DD`:

```bash
python feed.py --catch-up --since 2026-09-26
```

Nothing can be doubled up by this. Everything in that stretch is posted again
and everything already stored is refused on its id.

## The recorded days

```
STILLWATCH_DEMO_DATA   a folder for them, for example demo-data
```

Six days of a simulated household, each ending somewhere worth looking at.
They are built on first start if the folder is empty, because a container
begins with an empty disk every time it restarts and a few megabytes of events
that rebuild in a second do not belong in the repository.

They are readable without signing in, in every configuration, and are labelled
as demonstrations wherever they appear. Without this set, somebody who cannot
sign in sees a sign in panel and nothing else.

## Amazon services

Both are optional, both are read from the environment, and both need an AWS
access key with nothing on it but the one permission it uses.

```
AWS_ACCESS_KEY_ID            a key for a user that can do these two things only
AWS_SECRET_ACCESS_KEY
AWS_REGION                   where the topic lives
STILLWATCH_SNS_TOPIC_ARN     notifications through Amazon SNS
STILLWATCH_BEDROCK_MODEL_ID  have the opening sentence of a message written
STILLWATCH_BEDROCK_REGION    where the model lives, if not AWS_REGION
```

### SNS, for the messages

Create a standard topic, then let each caregiver subscribe themselves to it by
email or text. Stillwatch publishes to the topic and never holds an address or
a phone number, which is the whole reason to prefer it. SMTP is the fallback
and does hold them, in configuration.

The access key needs one statement: `sns:Publish` on that topic's ARN.

### Bedrock, for the wording

Naming a model in the environment is the whole setup. Serverless models enable
themselves in an account the first time they are called, and the model access
page that used to govern this has been retired.

Use the plain model id, `amazon.nova-lite-v1:0`. If the account can only reach
it through a cross region inference profile, Stillwatch finds that out on the
first failure and retries under `us.amazon.nova-lite-v1:0`, keeping whichever
name worked. Either value works, and neither has to be got right in advance.

`STILLWATCH_BEDROCK_REGION` exists because a region that carries a model is
not necessarily a region an account may call it in, and the SNS topic should
not have to move for the sake of a nicety. Set it when they differ.

Expect to have to find the working region by calling the model, not by asking
about it. On the account this was built for, `us-east-1` answered every
question correctly:

    aws bedrock get-foundation-model-availability       --region us-east-1 --model-id amazon.nova-lite-v1:0

    "authorizationStatus": "AUTHORIZED",
    "entitlementAvailability": "AVAILABLE",
    "regionAvailability": "AVAILABLE"

and then refused every call, as an administrator, under both model names:

    ValidationException: Operation not allowed

`us-west-2` answered in 378ms. The restriction is per account and per region,
it is reported by neither the availability API nor Service Quotas, and
`Operation not allowed` is the same sentence Bedrock uses for an unauthorized
account, an unavailable region and unaccepted terms. `stillwatch bedrock-check`
will separate those three. It will not catch this one, because the API it asks
does not know about it. Calling the model is the only test that does.

Stillwatch calls `converse` with the facts of a message and asks for the
opening sentence in a human voice. What comes back is checked before anyone sees it: too long,
more than one paragraph, a banned word, or any number that is not in the facts
it was given, and the deterministic sentence is used instead.

Every reason underneath that sentence is the engine's own and is sent exactly
as produced. A model that is slow, unavailable, switched off or simply wrong
costs a message its tone and none of its facts.

The access key needs `bedrock:InvokeModel` on that model, and nothing else.

Cost is a few hundred short calls a month. A household that never goes quiet
unexpectedly costs nothing at all, because nothing is written when there is
nothing to say.

## Showing it raise an alarm on a deployed service

A deployed household is well. To see it judged otherwise, stop the movement
without stopping the cameras:

```
python feed.py --catch-up --still --url https://your-domain.example/ring/events
```

The scheduled job normally posts movement and camera check ins together.
`--still` posts only the check ins, so the house has every reason to believe it
is being watched and nobody is moving through it. That is the one state this
product exists to recognise, and it is not the same as the feed stopping, which
reads as cameras that cannot be reached and is reported as such.

Run it in the middle of the day. The tolerated quiet at ten in the morning is
twenty odd minutes and at midnight it is hours, so a still house at night takes
all night to become interesting.

Put the job back to `python feed.py --catch-up` afterwards and the next run
fills the gap back in. To leave the gap where it was, add `--from-now`.

## Every setting

Everything the service reads from its environment, in one place. Nothing here
has a default worth relying on except where one is given.

### The home

| Setting | Default | What it does |
|---|---|---|
| `STILLWATCH_PERSON` | `The household` | Whose home this is, used in every message |
| `STILLWATCH_TZ` | UTC | The home's own timezone, for example `Africa/Douala`. Day boundaries and learned hours are read on this clock |
| `STILLWATCH_NOTIFY` | off | `1` to judge the house on a timer and send messages. Without it the service only answers the dashboard |

### Where events live

| Setting | Default | What it does |
|---|---|---|
| `DATABASE_URL` | none | Postgres. Set by most platforms automatically, and the right choice for any deployment, because a container's disk is wiped on restart |
| `STILLWATCH_DB` | `events.db` | A SQLite file, used only when there is no `DATABASE_URL` |

### Ring

| Setting | Default | What it does |
|---|---|---|
| `STILLWATCH_RING_CLIENT_ID` | none | From the Ring developer console |
| `STILLWATCH_RING_CLIENT_SECRET` | none | From the same place, shown once |
| `STILLWATCH_RING_WEBHOOK_SECRET` | none | The key deliveries are signed with. Without it `/ring/events` refuses everything |

### Who may look

| Setting | Default | What it does |
|---|---|---|
| `STILLWATCH_MEMBERS` | none | `Name:code, Name:code`. Each person signs in as themselves, and an answer carries their name |
| `STILLWATCH_PASSCODE` | none | One code for the whole household, used only if `STILLWATCH_MEMBERS` is unset |
| `STILLWATCH_SESSION_SECRET` | derived | Signs session cookies. Left unset, a key is derived from the webhook secret. Changing it signs everybody out |

With neither code set the household is open to anyone who knows the address,
and the page says so in a panel at the top of itself.

### Messages

| Setting | Default | What it does |
|---|---|---|
| `STILLWATCH_SNS_TOPIC_ARN` | none | The Amazon SNS topic to publish to. Caregivers subscribe themselves, so no address is ever stored here |
| `AWS_REGION` | none | Where the topic is. `AWS_DEFAULT_REGION` is read as a fallback |
| `STILLWATCH_SNS_ENDPOINT` | none | Point SNS somewhere else, for LocalStack |
| `STILLWATCH_NOTICE_CONSOLE` | off | `1` to print every message to the log as well |
| `STILLWATCH_NOTICE_LOG` | none | A file to append every message to, which doubles as the sent log |

The SMTP channel below is a fallback for a deployment that cannot use SNS. It
holds caregivers' addresses in configuration, which SNS does not, so prefer SNS
wherever it is available.

| Setting | Default | What it does |
|---|---|---|
| `STILLWATCH_SMTP_HOST` | none | Turns the email channel on |
| `STILLWATCH_SMTP_PORT` | `587` | |
| `STILLWATCH_SMTP_USER` | none | |
| `STILLWATCH_SMTP_PASSWORD` | none | |
| `STILLWATCH_EMAIL_TO` | none | Comma separated. Required alongside the host |
| `STILLWATCH_EMAIL_FROM` | the SMTP user | |

### The wording

| Setting | Default | What it does |
|---|---|---|
| `STILLWATCH_BEDROCK_MODEL_ID` | none | A model to write the opening sentence, for example `amazon.nova-lite-v1:0`. Unset, every message is the engine's own words |
| `STILLWATCH_BEDROCK_REGION` | `AWS_REGION` | Where to call the model, when that is not where the topic lives |

### The recorded days, and the feed

| Setting | Default | What it does |
|---|---|---|
| `STILLWATCH_DEMO_DATA` | none | A folder for the recorded days, built on first start if empty |
| `STILLWATCH_FEED_URL` | `http://127.0.0.1:8420/ring/events` | Where `feed.py` posts. Set it when the feed runs anywhere other than the machine serving the dashboard |

## Checking it works

```
GET https://your-domain.example/api/health
```

A deployed household, signed out, answers like this:

```json
{"ok": true, "store_ok": true, "source": "live", "webhook": true,
 "ring_linked": false, "stored_events": 0, "watching": false,
 "feed_recent": false, "judging_recently": false,
 "locked": true, "mode": "shared", "name": null, "scenarios": 1}
```

| Field | Means |
|---|---|
| `ok` | The service answered and could reach its own records |
| `store_ok` | The database is reachable. `false` with HTTP 503 when it is not |
| `webhook` | The Ring signing key is loaded |
| `ring_linked` | An account is connected. History is fetched at that moment, so a baseline exists from the start rather than weeks later |
| `watching` | The judge has run at least once |
| `feed_recent` | Something arrived from the cameras within the last six hours |
| `judging_recently` | The judge ran within the last six hours |
| `locked` | The household view is behind a sign in |
| `mode` | `members` for a code each, `shared` for one code, `open` for neither |

**When anybody last moved is not in there**, and that is deliberate. It is the
occupancy record, and this endpoint is open, so handing out the timestamp would
let anyone poll once a minute and read off when the house went quiet and when
it woke. `feed_recent` and `judging_recently` answer the operational question
without answering that one. The exact times appear only once you are signed in.

Monitor `ok`, `judging_recently` and `feed_recent`. A watcher that has quietly
died looks exactly like a household where nothing is wrong, which is the one
failure this product cannot afford, and `stillwatch watchdog` exists to shout
about it.

If the database becomes unreachable, this endpoint is the one thing that keeps
answering: 503, with `ok: false` and `store_ok: false`, so the part that is
down is named rather than guessed at. Other API requests fail with a sentence
saying the fault is Stillwatch's own and is not a reading of the home.

## A first day

A newly connected home has no history, so Stillwatch says so rather than
guessing. It needs a couple of weeks before its judgements mean much, which is
why the first link pulls whatever Ring already holds.
