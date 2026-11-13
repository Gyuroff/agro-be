# agro-be

Python бекенд на Agro ERP. Спецификацията е в съседното хранилище `agro-app` (`../agro-app/docs/`), правилата за работа — в `AGENTS.md`.

## Локален старт

Нужни: Docker (с Compose v2) и [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env        # попълни стойностите (тайни само тук, никога в git)
make up                     # postgres 16, redis 7, greenmail — чака healthy
make psql-test              # провери, че agro_test съществува
make migrate                # alembic upgrade head (след agro-zaz.1 / agro-l2v.1)
make test                   # pytest (без мрежа и истински LLM)
```

Услуги и портове: Postgres `5432` (бази `agro`, `agro_test`, потребител/парола `agro`), Redis `6379`, GreenMail IMAP `3143` / SMTP `3025` (потребител `orders` / парола `orders@agroimport.test`).

Ако порт на хоста е зает (`port is already allocated`), смени го с променлива, напр. `REDIS_PORT=6380 make up`
(или я сложи в `.env`). Променливите: `POSTGRES_PORT`, `REDIS_PORT`, `GREENMAIL_SMTP_PORT`,
`GREENMAIL_IMAP_PORT`, `API_PORT`. Тогава и `APP_REDIS_URL` / `APP_DATABASE_URL` за тестовете от хоста
трябва да сочат към новия порт. Кой процес държи порта: `lsof -nP -iTCP:6379 -sTCP:LISTEN`.

Приложението в контейнери (след като съществува кодът):

```bash
make app                    # api :8000, celery worker, celery beat
make logs
make app-down
```

Други команди: `make down`, `make reset` (трие томовете), `make lint`, `make check`.

`docker/postgres/init.sh` се изпълнява само при първо създаване на тома; за повторно изпълнение — `make reset && make up`.
