#!/usr/bin/env bash
# End-to-end walkthrough of the Wallet Service API using curl.
#
# Runs every endpoint in a sensible order and demonstrates the hard cases:
# idempotent retry (no double charge), idempotency conflict, and the balance
# constraint (a deduction that would go negative is rejected).
#
# Each step prints the exact curl command (in yellow) before running it, so you
# can copy-paste any command into a separate terminal and run it yourself.
#
# Prerequisites: the service is running (./startup.sh or `make run`).
# Optional: `jq` for pretty JSON (falls back to raw output if absent).
#
# Usage:  ./demo.sh            # against http://localhost:5000
#         BASE_URL=http://host:port ./demo.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:5000}"

# Idempotency keys are persisted forever, so a fixed key would collide on a
# second run. Scope the order ids to this run to keep the demo repeatable.
RUN_ID="$(date +%Y%m%d-%H%M%S)"
ORDER_ID="order-$RUN_ID"

# Pretty-print JSON if jq is available, otherwise pass the body through unchanged.
if command -v jq >/dev/null 2>&1; then
  pretty() { jq; }
else
  pretty() { cat; }
fi

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

# Print a copy-pasteable command in yellow.
show() { printf '\033[0;33m$ %s\033[0m\n' "$1"; }

# Show a curl command, then run it and pretty-print the response.
run() {
  show "$1"
  eval "$1" | pretty
}

step "0. Health check"
run "curl -s $BASE_URL/health"

step "1. Create a wallet"
CREATE_CMD="curl -s -X POST $BASE_URL/wallets \
  -H 'Content-Type: application/json' \
  -d '{\"customer_id\": \"acme-logistics\"}'"
show "$CREATE_CMD"
CREATE_RESP=$(eval "$CREATE_CMD")
echo "$CREATE_RESP" | pretty
WALLET_ID=$(echo "$CREATE_RESP" | jq -r .id 2>/dev/null || true)
if [ -z "${WALLET_ID:-}" ] || [ "$WALLET_ID" = "null" ]; then
  echo "Could not parse wallet id (is jq installed and the service up?)." >&2
  exit 1
fi
echo "wallet id: $WALLET_ID"

step "2. Top up 25000 paise (Rupees 250)"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/topup \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 25000}'"

step "3. Check balance (expect 25000)"
run "curl -s $BASE_URL/wallets/$WALLET_ID/balance"

step "4. Deduct 10000 paise (Rupees 100) for $ORDER_ID (idempotent)"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/deduct \
  -H 'Idempotency-Key: $ORDER_ID' \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 10000, \"reference_id\": \"$ORDER_ID\"}'"

step "5. Retry the SAME $ORDER_ID -> HTTP 200 replay, no second charge"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/deduct \
  -H 'Idempotency-Key: $ORDER_ID' \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 10000, \"reference_id\": \"$ORDER_ID\"}'"

step "6. Balance after one order (expect 15000)"
run "curl -s $BASE_URL/wallets/$WALLET_ID/balance"

step "7. Idempotency conflict: same key, different amount -> HTTP 409"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/deduct \
  -H 'Idempotency-Key: $ORDER_ID' \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 20000, \"reference_id\": \"$ORDER_ID\"}'"

step "8. Drain the wallet: second deduction has insufficient balance -> HTTP 422"
for i in 1 2; do
  drain_id="$ORDER_ID-drain-$i"
  echo "-- $drain_id"
  run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/deduct \
  -H 'Idempotency-Key: $drain_id' \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 10000, \"reference_id\": \"$drain_id\"}'"
done

step "9. Transaction history (ledger, newest first)"
run "curl -s $BASE_URL/wallets/$WALLET_ID/transactions"

step "Done. Final balance:"
run "curl -s $BASE_URL/wallets/$WALLET_ID/balance"
