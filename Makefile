.PHONY: install run seed test lint eval eval-support eval-retrieval eval-replay eval-business sample-bad-cases migrate compose-up compose-down manual

install:
	python -m pip install -e ".[dev]"

run:
	uvicorn app.asgi:app --reload --port 8000

seed:
	python scripts/seed.py

test:
	python -m pytest -q

lint:
	ruff check app tests scripts evals alembic

eval:
	python evals/run.py

eval-support:
	python evals/support_run.py

eval-retrieval:
	python evals/retrieval_metrics.py

eval-replay:
	python evals/replay.py --self-check

eval-business:
	python evals/business_metrics.py

sample-bad-cases:
	python scripts/sample_traces.py

migrate:
	alembic upgrade head

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down

manual:
	python scripts/build_manual.py
