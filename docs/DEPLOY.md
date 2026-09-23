# Putting Stillwatch online

Ring will not let an account be linked until four HTTPS addresses exist that we
host, and it does not accept `localhost`. So this has to happen before any real
event can arrive.

The server runs Ubuntu with Postgres on the same machine and Caddy in front of
it, because Caddy obtains and renews the HTTPS certificate on its own. One
script does all of it.

## What you need first

- An AWS account with credits, which we have
- A domain name, because a certificate cannot be issued for an `amazonaws.com`
  address. `stillwatch.tech` from the GitHub Student Pack, or see the fallback
  at the bottom if that is not ready

## 1. Start the server

In the AWS console, go to **EC2** and choose **Launch instance**.

| Setting | Value |
|---|---|
| Name | `stillwatch` |
| Application and OS image | **Ubuntu Server 24.04 LTS**, 64-bit x86 |
| Instance type | `t3.small` |
| Key pair | Create one called `stillwatch`, download the `.pem`, and keep it somewhere safe |
| Allow SSH traffic from | Anywhere |
| Allow HTTP traffic | Ticked |
| Allow HTTPS traffic | Ticked |

Leave everything else alone and choose **Launch instance**.

## 2. Give it an address that will not change

A restarted instance gets a new address, which would break DNS and the
certificate. So:

**EC2 → Elastic IPs → Allocate Elastic IP address → Allocate.** Select it, then
**Actions → Associate Elastic IP address**, choose the `stillwatch` instance,
and associate.

Write that address down. Everything below refers to it.

## 3. Point the domain at it

At your domain registrar, add one record:

| Type | Name | Value |
|---|---|---|
| A | `@` | the Elastic IP |

DNS usually takes a few minutes. You can check from your own machine with
`nslookup stillwatch.tech` until it answers with that address.

## 4. Install everything

**EC2 → Instances →** select `stillwatch` **→ Connect → EC2 Instance Connect →
Connect.** A terminal opens in the browser. No keys, no SSH client.

Paste these two lines:

```bash
curl -fsSL https://raw.githubusercontent.com/kadian-arch/stillwatch/main/deploy/setup.sh -o setup.sh
```

```bash
sudo bash setup.sh stillwatch.tech
```

It installs Python, Postgres, Caddy and the service itself, creates the
database with a generated password, and starts everything. It takes two or
three minutes and asks nothing.

It finishes by telling you whether the service is answering.

## 5. Put the Ring credentials in

```bash
sudo nano /etc/stillwatch.env
```

Fill in the three Ring lines from the developer console. The HMAC signature key
is the one shown only once when the app was created.

```
STILLWATCH_RING_CLIENT_ID=...
STILLWATCH_RING_CLIENT_SECRET=...
STILLWATCH_RING_WEBHOOK_SECRET=...
STILLWATCH_PERSON=Margaret
```

Save with `Ctrl+O`, `Enter`, then exit with `Ctrl+X`. Then:

```bash
sudo systemctl restart stillwatch
```

This file is readable only by root and is never in the repository.

## 6. Check it from outside

Open `https://stillwatch.tech/api/health` in your browser. You should see
something like:

```json
{"ok": true, "source": "live", "webhook": true, "ring_linked": false, "stored_events": 0}
```

`webhook: true` means the signature key is loaded and the endpoint will accept
deliveries. `ring_linked: false` is expected until the next step.

If the page does not load, the certificate may still be being issued. Wait two
minutes and try again. If it still fails:

```bash
sudo journalctl -u caddy -n 30 --no-pager
```

## 7. Give Ring the four addresses

In the Ring developer console, under **Account linking**:

| Field | Value |
|---|---|
| Account Link URL | `https://stillwatch.tech/ring/link` |
| Default Redirect URL | `https://stillwatch.tech/ring/linked` |
| Token Exchange URL | `https://stillwatch.tech/ring/token` |
| Webhook URL | `https://stillwatch.tech/ring/events` |

For **data access and API scopes**, choose motion events and doorbell presses
only. Nothing to do with video. We never need it, and asking for the minimum is
part of what the product claims.

## 8. Link the account, then fetch the past

Once linking is complete, check `https://stillwatch.tech/api/health` again and
`ring_linked` should be `true`. Then pull whatever history Ring already holds:

```bash
cd /opt/stillwatch && sudo -u stillwatch .venv/bin/python -m stillwatch backfill --days 30
```

It prints how many events came back and how many days they span.

## Afterwards

**To update the running service** after new code is pushed:

```bash
sudo bash /opt/stillwatch/deploy/setup.sh stillwatch.tech
```

It pulls the latest code and restarts. It is safe to run repeatedly and will
not touch the settings file or the database.

**To see what the service is doing:**

```bash
sudo journalctl -u stillwatch -f
```

**If the domain is not ready**, use a hostname that resolves to your address
without any registrar, replacing the dots with dashes:

```bash
sudo bash setup.sh 13-51-22-9.sslip.io
```

That gets a real certificate and works with Ring. Switch to the proper domain
later by running the script again with the new name.
