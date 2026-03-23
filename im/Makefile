SHELL := /bin/bash
ROOT := /im
COMPOSE := docker compose --env-file $(ROOT)/.env -f $(ROOT)/docker-compose.yml

.PHONY: init up down restart logs ps health test backup restore modules

init:
	bash $(ROOT)/scripts/init.sh

up:
	bash $(ROOT)/scripts/deploy.sh

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

health:
	bash $(ROOT)/scripts/healthcheck.sh

test:
	docker compose --env-file $(ROOT)/.env -f $(ROOT)/docker-compose.yml run --rm adapter pytest -q

backup:
	bash $(ROOT)/scripts/backup.sh

restore:
	bash $(ROOT)/scripts/restore.sh $(DIR)

modules:
	bash $(ROOT)/scripts/install_humhub_modules.sh
