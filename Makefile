# EvalKit Makefile

.PHONY: dev migrate seed test lint eval eval-nightly gate gen-cases align example

dev:
	uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

migrate:
	python -c "from backend.app.db.session import init_db; init_db(); print('Database schema up to date!')"

seed:
	python -m backend.app.db.seed
	python -m examples.sample_app.seed_dataset

test:
	pytest backend/tests/ -v

lint:
	python -m ruff check backend sdk/python || true

eval:
	python backend/cli.py run --dataset sample_golden_suite --level 1 --executor fake

eval-nightly:
	python backend/cli.py run --dataset sample_golden_suite --level 2 --executor fake

gate:
	python backend/cli.py gate --run-id latest

gen-cases:
	python backend/cli.py generate --dataset sample_golden_suite --feature $(FEATURE) --scenario $(SCENARIO) --count $(N)

align:
	python backend/cli.py align --judge-id $(JUDGE)

example:
	python -m examples.sample_app.app
