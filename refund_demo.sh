#!/usr/bin/env bash
# End-to-end walkthrough of the Wallet Service *refund* flow using curl.
#
# Demonstrates every refund rule:
#   - refunding a DEDUCT credits the wallet back
#   - refunding a TOPUP debits the wallet
#   - idempotent retry returns the original response (no double refund)
#   - a transaction can be refunded at most once (409)
#   - refunding a TOPUP that would overdraw is rejected (422)
#   - refunding an unknown transaction is rejected (404)
#
# Each step prints the exact curl command (in yellow) before running it.
#
# Prerequisites:
#   - migrations are applied (`make migrate`) because refunds add DB columns
#   - the service is running (./startup.sh or `make run`)
#   - `jq` is required to parse transaction ids
#
# Usage:  ./refund_demo.sh            # against http://localhost:5000
#         BASE_URL=http://host:port ./refund_demo.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:5000}"

# Idempotency keys are persisted forever, so scope them to this run to stay repeatable.
RUN_ID="$(date +%Y%m%d-%H%M%S)"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required for this demo (it parses transaction ids). Please install jq." >&2
  exit 1
fi

pretty() { jq; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
show() { printf '\033[0;33m$ %s\033[0m\n' "$1"; }

# Show a curl command, run it, pretty-print, and also return the raw body via a global.
LAST_BODY=""
run() {
  show "$1"
  LAST_BODY="$(eval "$1")"
  echo "$LAST_BODY" | pretty
}

step "0. Health check"
run "curl -s $BASE_URL/health"

step "1. Create a wallet"
run "curl -s -X POST $BASE_URL/wallets \
  -H 'Content-Type: application/json' \
  -d '{\"customer_id\": \"acme-logistics\"}'"
WALLET_ID="$(echo "$LAST_BODY" | jq -r .id)"
if [ -z "${WALLET_ID:-}" ] || [ "$WALLET_ID" = "null" ]; then
  echo "Could not parse wallet id (is the service up and migrated?)." >&2
  exit 1
fi
echo "wallet id: $WALLET_ID"

step "2. Top up 25000 paise (Rupees 250)"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/topup \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 25000}'"

ORDER_ID="order-$RUN_ID"
step "3. Deduct 10000 paise (Rupees 100) for $ORDER_ID"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/deduct \
  -H 'Idempotency-Key: $ORDER_ID' \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 10000, \"reference_id\": \"$ORDER_ID\"}'"
DEDUCT_TX="$(echo "$LAST_BODY" | jq -r .id)"
echo "deduct transaction id: $DEDUCT_TX"

step "4. Balance after deduct (expect 15000)"
run "curl -s $BASE_URL/wallets/$WALLET_ID/balance"

REFUND_KEY="refund-$RUN_ID-1"
step "5. Refund the DEDUCT -> wallet is credited back (expect 201, balance_after 25000)"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/refund \
  -H 'Idempotency-Key: $REFUND_KEY' \
  -H 'Content-Type: application/json' \
  -d '{\"original_transaction_id\": \"$DEDUCT_TX\", \"reason\": \"customer cancelled order\"}'"

step "6. Retry the SAME refund key -> HTTP 200 replay, no second credit"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/refund \
  -H 'Idempotency-Key: $REFUND_KEY' \
  -H 'Content-Type: application/json' \
  -d '{\"original_transaction_id\": \"$DEDUCT_TX\", \"reason\": \"customer cancelled order\"}'"

step "7. Refund the same DEDUCT again with a NEW key -> HTTP 409 already refunded"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/refund \
  -H 'Idempotency-Key: refund-$RUN_ID-2' \
  -H 'Content-Type: application/json' \
  -d '{\"original_transaction_id\": \"$DEDUCT_TX\"}'"

step "8. Balance after refund (expect 25000, fully restored)"
run "curl -s $BASE_URL/wallets/$WALLET_ID/balance"

step "9. Top up 5000 paise, then refund that TOPUP -> wallet is debited"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/topup \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 5000}'"
TOPUP_TX="$(echo "$LAST_BODY" | jq -r .id)"
echo "topup transaction id: $TOPUP_TX"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/refund \
  -H 'Idempotency-Key: refund-$RUN_ID-topup' \
  -H 'Content-Type: application/json' \
  -d '{\"original_transaction_id\": \"$TOPUP_TX\", \"reason\": \"topup reversal\"}'"

step "10. Balance after topup refund (expect 25000 again)"
run "curl -s $BASE_URL/wallets/$WALLET_ID/balance"

step "11. Overdraw guard: a big topup, spend it, then a topup refund is rejected (HTTP 422)"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/topup \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 100000}'"
BIG_TOPUP_TX="$(echo "$LAST_BODY" | jq -r .id)"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/deduct \
  -H 'Idempotency-Key: spend-$RUN_ID' \
  -H 'Content-Type: application/json' \
  -d '{\"amount_paise\": 100000, \"reference_id\": \"spend-$RUN_ID\"}'"
echo "-- refunding the big topup would drive the balance negative:"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/refund \
  -H 'Idempotency-Key: refund-$RUN_ID-overdraw' \
  -H 'Content-Type: application/json' \
  -d '{\"original_transaction_id\": \"$BIG_TOPUP_TX\"}'"

step "12. Refund an unknown transaction -> HTTP 404"
run "curl -s -X POST $BASE_URL/wallets/$WALLET_ID/refund \
  -H 'Idempotency-Key: refund-$RUN_ID-missing' \
  -H 'Content-Type: application/json' \
  -d '{\"original_transaction_id\": \"00000000-0000-0000-0000-000000000000\"}'"

step "13. Transaction history (ledger, newest first) -- note the immutable refund rows"
run "curl -s $BASE_URL/wallets/$WALLET_ID/transactions"

step "Done. Final balance:"
run "curl -s $BASE_URL/wallets/$WALLET_ID/balance"
