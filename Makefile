.PHONY: up down reset logs ps app app-down psql psql-test migrate downgrade test lint fmt check

# Инфраструктура: postgres, redis, greenmail (чака healthy)
up:
	docker compose up -d --wait

down:
	docker compose --profile app down

# ВНИМАНИЕ: трие томовете (база, redis). Нужно, ако init.sh трябва да се изпълни наново.
reset:
	docker compose --profile app down -v

logs:
	docker compose --profile app logs -f --tail=100

ps:
	docker compose --profile app ps

# Приложението в контейнери (api :8000, worker, beat)
app:
	docker compose --profile app up -d --build --wait

app-down:
	docker compose --profile app stop api worker beat

psql:
	docker compose exec postgres psql -U agro -d agro

psql-test:
	docker compose exec postgres psql -U agro -d agro_test

# Командите по-долу вървят на хоста през uv (изискват pyproject.toml — задача agro-zaz.1)
migrate:
	uv run alembic upgrade head

downgrade:
	uv run alembic downgrade -1

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check . && uv run pyright

fmt:
	uv run ruff check --fix . && uv run ruff format .

# Всичко от „Преди затваряне“ в AGENTS.md
check: lint test
