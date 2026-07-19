-- Handy inspection queries for the Wallet Service database.
-- Paste these into pgAdmin (or any SQL client) connected to Postgres on
-- localhost:5432 (user/pass/db = wallet) to eyeball the current state:
--   alembic_version    -> which migration is applied
--   wallets            -> current balances
--   transactions       -> the append-only ledger (money movements)
--   processed_requests -> idempotency records (retries return these)

SELECT * FROM public.alembic_version;

SELECT * FROM public.wallets;

SELECT * FROM public.transactions ORDER BY created_at DESC;

SELECT * FROM public.processed_requests ORDER BY created_at DESC;
