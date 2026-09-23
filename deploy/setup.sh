#!/usr/bin/env bash
# Sets up Stillwatch on a fresh Ubuntu server: Python, Postgres, the service
# itself, and Caddy in front of it for HTTPS.
#
# Run it as:
#   sudo bash setup.sh stillwatch.tech
#
# Safe to run more than once. Nothing here asks a question, so it can be left
# to finish on its own.

set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "usage: sudo bash setup.sh <domain>" >&2
  echo "   eg: sudo bash setup.sh stillwatch.tech" >&2
  exit 2
fi

REPO="${STILLWATCH_REPO:-https://github.com/kadian-arch/stillwatch.git}"
APP_DIR=/opt/stillwatch
ENV_FILE=/etc/stillwatch.env
DB_NAME=stillwatch
DB_USER=stillwatch

echo "==> installing what we need"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git postgresql postgresql-contrib \
  debian-keyring debian-archive-keyring apt-transport-https curl

if ! command -v caddy >/dev/null 2>&1; then
  echo "==> installing Caddy, which gets the HTTPS certificate on its own"
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -qq
  apt-get install -y -qq caddy
fi

echo "==> setting up the database"
systemctl enable --now postgresql
DB_PASS=$(sudo -u postgres psql -tAc \
  "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 && echo "keep" || openssl rand -hex 24)

if [ "$DB_PASS" = "keep" ]; then
  echo "    database user already exists, leaving its password alone"
  DB_URL=$(grep -E "^DATABASE_URL=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
  if [ -z "$DB_URL" ]; then
    echo "    but no DATABASE_URL was saved, so setting a new password"
    DB_PASS=$(openssl rand -hex 24)
    sudo -u postgres psql -qc "ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASS}';"
    DB_URL="postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}"
  fi
else
  sudo -u postgres psql -qc "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';"
  sudo -u postgres psql -qc "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
  DB_URL="postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}"
fi

echo "==> fetching the code"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin
  git -C "$APP_DIR" reset --hard --quiet origin/main
else
  rm -rf "$APP_DIR"
  git clone --quiet "$REPO" "$APP_DIR"
fi

echo "==> installing the application"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt" boto3

id stillwatch >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin stillwatch
chown -R stillwatch:stillwatch "$APP_DIR"

echo "==> writing the settings file"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
# Stillwatch settings. Nothing in here belongs in the repository.
DATABASE_URL=${DB_URL}
STILLWATCH_PERSON=The household

# From the Ring developer console. Fill these in, then:
#   sudo systemctl restart stillwatch
STILLWATCH_RING_CLIENT_ID=
STILLWATCH_RING_CLIENT_SECRET=
STILLWATCH_RING_WEBHOOK_SECRET=

# Optional. Amazon SNS for real notifications, Bedrock for the wording.
AWS_REGION=us-east-1
STILLWATCH_SNS_TOPIC_ARN=
STILLWATCH_BEDROCK_MODEL_ID=
EOF
  chmod 600 "$ENV_FILE"
  echo "    wrote $ENV_FILE with the database already filled in"
else
  grep -q "^DATABASE_URL=" "$ENV_FILE" || echo "DATABASE_URL=${DB_URL}" >> "$ENV_FILE"
  echo "    kept the existing $ENV_FILE"
fi

echo "==> installing the service"
install -m 644 "$APP_DIR/deploy/stillwatch.service" /etc/systemd/system/stillwatch.service
systemctl daemon-reload
systemctl enable --now stillwatch
systemctl restart stillwatch

echo "==> putting Caddy in front of it for $DOMAIN"
sed "s/DOMAIN_HERE/${DOMAIN}/g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

echo
echo "done."
echo
sleep 3
if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  echo "  the service is answering on the machine itself"
else
  echo "  the service is NOT answering. Look at why with:"
  echo "    sudo journalctl -u stillwatch -n 40 --no-pager"
fi
echo
echo "  next: point ${DOMAIN} at this server's address, then open"
echo "    https://${DOMAIN}/api/health"
echo "  the certificate arrives on its own once the name resolves here."
