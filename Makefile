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

smoke: ## Run the demo-path smoke test (P10 supplies it)
	./scripts/smoke.sh

clean: ## Stop everything and DESTROY the database volume
	$(COMPOSE) down -v
