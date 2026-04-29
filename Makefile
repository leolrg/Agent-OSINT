.PHONY: help dev down logs test smoke

help:
	@echo "make dev       - bring up the local stack (postgres+redis+localstack+migrate+worker)"
	@echo "make down      - stop and remove all local-stack containers"
	@echo "make logs      - tail logs from all services"
	@echo "make test      - run pytest (unit + deploy tests)"
	@echo "make smoke     - end-to-end test: push an SQS message, observe completion"

dev:
	docker compose up -d

down:
	docker compose down -v

logs:
	docker compose logs -f --tail=100

test:
	pytest -v

smoke:
	./scripts/smoke_test.sh
