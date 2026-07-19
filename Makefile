.PHONY: help up down run migrate test stub

help:
	@echo "make up       - start Postgres + Redis (docker compose)"
	@echo "make down     - stop and remove containers"
	@echo "make migrate  - apply database migrations (alembic)"
	@echo "make run      - run the Wallet Service locally"
	@echo "make test     - run the test suite"
	@echo "make stub     - run the Order Service stub against a running service"

up:
	docker compose up -d
	@echo "Postgres on :5432, Redis on :6379"

down:
	docker compose down

migrate:
	alembic upgrade head

run:
	python -m wallet_service.app

test:
	pytest -v

stub:
	python order_service_stub.py
