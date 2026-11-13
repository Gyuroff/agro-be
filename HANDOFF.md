# HANDOFF — състояние към 2026-11-13 (след agro-zaz.1 / agro-l2v.1)

Статус, не спецификация; изтрий го, когато е остарял.

## Готово и проверено
- `make check` зелено (ruff, ruff format, pyright, pytest — 33 теста срещу Postgres от docker-compose).
- `alembic upgrade head`, `downgrade -1`, `downgrade base`, `alembic check` (няма разлика модели↔миграции).
- `uvicorn app.main:create_app --factory` → `GET /health` = `{"status":"ok","db":true}`.
- Затворени: `agro-4ea`, `agro-zaz.1`, `agro-l2v.1`.

## Решения, които не личат от кода
- Ruff: RUF001–003 изключени (кирилицата в текстовете е нарочна); `alembic` е `known-third-party`
  (папката `alembic/` иначе обърква isort). line-length 110, pyright strict за `app/core` и `app/domain`
  — по критериите на `agro-zaz.1`.
- `created_by`/`updated_by` са `viewonly=True` + `lazy="raise"`: id-тата пише само before_flush.
- Тестовите модели с mixin-ите са в `tests/audited_models.py` (отделна metadata, свой `Actor` към
  същата таблица); интеграционните тестове създават таблицата си в транзакцията на теста.
- Локален `.env` (в .gitignore) със случайни тайни — нужен за `alembic`/`uvicorn` от хоста; pytest не го ползва.

## Известни отклонения от спецификацията (за преглед)
- `actors.user_id` е без FK към `users` — FK се добавя в миграцията на `agro-l2v.3`.
- Миграция 0001 създава и `citext` (по задачата `agro-l2v.1`), въпреки че data-model.md §0 описва имейли
  като lowercase `text` + UNIQUE, без citext.
- Healthcheck на greenmail е `disable: true` (образът няма nc/curl).
- `/health` не е описан в `docs/api.md`; формата е от `agro-zaz.1`.

## Следва
- `agro-l2v.2` (одитен контекст, before/after_flush, `audit_log`), после `l2v.3`…`l2v.13`.

## Правила, които лесно се нарушават
Не измисляй колони/статуси/маршрути (`bd note <id> "ВЪПРОС: …"`); статуси се менят само в `app/services/`;
пари само `Decimal`, в JSON низове; без `update()`/`delete()` върху одитирани модели; време само през
`app.core.clock`; един commit на заявка (в `get_db`/`session_scope`, не в сервизите).
