SHELL := /bin/bash
COMPOSE := docker compose
ALEMBIC := alembic -c packages/core/alembic.ini
CITY ?= abuja

.PHONY: help up down build logs ps db-init db-revision test seed shell-api shell-processing psql smoke clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## Build and start every service
	$(COMPOSE) up -d --build

down: ## Stop every service (keeps the database volume)
	$(COMPOSE) down

build: ## Rebuild images without starting
	$(COMPOSE) build

logs: ## Tail logs from every service
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

db-init: ## Apply all migrations (P1 supplies the first one)
	$(COMPOSE) exec api $(ALEMBIC) upgrade head

db-revision: ## Autogenerate a migration: make db-revision M="add road_segments"
	$(COMPOSE) exec api $(ALEMBIC) revision --autogenerate -m "$(M)"

test: ## Run the test suite (api + core in the api container, pipelines in processing)
	$(COMPOSE) exec api pytest -q services/api/tests packages/core/tests
	$(COMPOSE) exec processing pytest -q services/processing/tests
	$(COMPOSE) exec alerts pytest -q services/alerts/tests

seed: ## Run the full ingestion pipeline for $(CITY)
	$(COMPOSE) exec processing python -m processing.ingest.roads --city $(CITY)
	$(COMPOSE) exec processing python -m processing.ingest.terrain --city $(CITY)
	$(COMPOSE) exec processing python -m processing.ingest.wofs --city $(CITY)
	$(COMPOSE) exec processing python -m processing.scoring.susceptibility_v2 --city $(CITY)
	$(COMPOSE) exec processing python -m processing.scoring.daily --city $(CITY)

shell-api: ## Shell into the api container
	$(COMPOSE) exec api bash

shell-processing: ## Shell into the processing container
	$(COMPOSE) exec processing bash

psql: ## Open psql against the database
	$(COMPOSE) exec db psql -U climatepass -d climatepass

smoke: ## Run the demo-path smoke test (under 30s)
	./scripts/smoke.sh

demo-up: ## Start in DEMO_MODE on an air-gapped network (no internet at all)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.demo.yml up -d

demo-verify: ## Prove the demo path works with no internet at all
	./scripts/verify-demo.sh

# Only our own tables. A plain pg_dump also carries the tiger and topology
# schemas that the local PostGIS image installs, and a managed Postgres
# (Render, RDS) has no postgis_tiger_geocoder to restore them into.
DUMP_TABLES := -t cities -t road_segments -t segment_risk -t subscriptions -t alerts -t ingestion_runs -t alembic_version

dump-db: ## Dump the database to deploy/seed.sql.gz (portable; seed production from this)
	@mkdir -p deploy
	$(COMPOSE) exec -T db pg_dump -U climatepass --no-owner --no-acl $(DUMP_TABLES) climatepass | gzip > deploy/seed.sql.gz
	@echo "wrote deploy/seed.sql.gz ($$(du -h deploy/seed.sql.gz | cut -f1))"
	@echo "restore needs PostGIS present first: CREATE EXTENSION IF NOT EXISTS postgis;"

seed-remote: ## Seed a managed Postgres: make seed-remote DB='postgres://...'
	@test -n "$(DB)" || (echo "usage: make seed-remote DB='postgres://...'" && exit 1)
	./scripts/seed-remote.sh "$(DB)"

restore-db: ## Restore deploy/seed.sql.gz into the running database
	@test -f deploy/seed.sql.gz || (echo "deploy/seed.sql.gz not found — run make dump-db first" && exit 1)
	gunzip -c deploy/seed.sql.gz | $(COMPOSE) exec -T db psql -U climatepass -d climatepass
	@echo "restored. Run 'make smoke' to confirm."

prod-up: ## Start the production stack (needs PUBLIC_DOMAIN and WEBHOOK_HMAC_SECRET)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml up -d --build

clean: ## Stop everything and DESTROY the database volume
	$(COMPOSE) down -v
