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

Set two things and it is not:

```
STILLWATCH_PASSCODE          something long that the family can read down a phone
STILLWATCH_SESSION_SECRET    a long random string, used to sign session cookies
```

With a passcode set, the household and every answer need a sign in. Anybody
holding the passcode signs in with their own name, and that name is stored
with anything they answer and sent to everybody else, so the family can see
who has already been round. Sessions last a fortnight and are a signed cookie,
so a restart does not sign anyone out and there is no session table to leak.

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

## Amazon services

Both are optional, and both are read from the environment.

```
STILLWATCH_SNS_TOPIC_ARN     notifications through Amazon SNS
AWS_REGION                   the region that topic and model live in
STILLWATCH_BEDROCK_MODEL_ID  have the opening sentence of a message written
```

SNS is the better notification channel because a caregiver subscribes
themselves to the topic, so Stillwatch never holds anybody's address or phone
number. SMTP is the fallback, and it does hold them.

Bedrock rewrites the first sentence of a message and nothing else. Every
reason underneath it is the engine's own and is sent exactly as produced, so a
model that is slow, unavailable or switched off costs a message its tone and
none of its facts.

## Checking it works

```
GET https://your-domain.example/api/health
```

```json
{"ok": true, "source": "live", "webhook": true, "ring_linked": false, "stored_events": 0}
```

`webhook: true` means the signing key is loaded. `ring_linked` turns true once
an account is connected, and history is fetched at that moment so a baseline
exists from the start rather than weeks later.

## A first day

A newly connected home has no history, so Stillwatch says so rather than
guessing. It needs a couple of weeks before its judgements mean much, which is
why the first link pulls whatever Ring already holds.
