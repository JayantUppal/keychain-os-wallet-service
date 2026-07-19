#!/usr/bin/env bash
# Start Postgres + Redis, install deps, then run the Wallet Service.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Starting Postgres and Redis (docker compose)..."
docker compose up -d

echo "==> Waiting for Postgres and Redis to be healthy..."
until docker compose ps --format '{{.Health}}' | grep -q "healthy" && \
      [ "$(docker compose ps --format '{{.Health}}' | grep -c healthy)" -ge 2 ]; do
  sleep 1
done
echo "    services are healthy."

if [ ! -d ".venv" ]; then
  echo "==> Creating virtualenv..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "==> Created .env from .env.example"
fi

echo "==> Applying database migrations..."
alembic upgrade head

echo "==> Starting Wallet Service on http://localhost:${FLASK_PORT:-5000}"
python -m wallet_service.app
