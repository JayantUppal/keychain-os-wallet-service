.PHONY: help up down run restart migrate test stub demo demo-refund

help:
	@echo "make up          - start Postgres + Redis (docker compose)"
	@echo "make down        - stop and remove containers"
	@echo "make migrate     - apply database migrations (alembic)"
	@echo "make run         - run the Wallet Service locally"
	@echo "make restart     - rebuild + restart the dockerized service (applies migrations on boot)"
	@echo "make test        - run the test suite"
	@echo "make stub        - run the Order Service stub against a running service"
	@echo "make demo        - run the curl walkthrough against a running service"
	@echo "make demo-refund - migrate, then run the refund curl walkthrough"

up:
	docker compose up -d
	@echo "Postgres on :5432, Redis on :6379"

down:
	docker compose down

migrate:
	alembic upgrade head

run:
	python -m wallet_service.app

# Rebuild and restart the dockerized service so it picks up the latest code; its boot
# command runs `alembic upgrade head`, applying the refund schema change.
restart:
	docker compose --profile full up -d --build wallet
	@echo "Wallet Service (re)started with migrations applied."

test:
	pytest -v

stub:
	python order_service_stub.py

demo:
	./demo.sh

# Refunds add DB columns, so apply migrations before the walkthrough. If you run the
# service locally with `make run`, restart it after migrating so it serves the new code.
demo-refund: migrate
	./refund_demo.sh
