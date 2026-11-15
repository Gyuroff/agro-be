# HANDOFF — състояние към 2026-11-15 (след agro-zaz.1 / l2v.1 / l2v.2)

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

## agro-l2v.2 (одит) — готово
- `app/core/audit_context.py`, `app/db/audit.py` (слушатели се регистрират при импорт на `app.db`), `app/models/audit.py`,
  миграция `0003` (таблица + тригер `audit_log_immutable`), `tests/integration/test_audit.py`, `tests/test_no_bulk_dml.py`.
- Тестове, които пишат в одитирани таблици, ползват фикстурата `audit_ctx` (source=seed); без нея → `MissingAuditContext`.
- Одитират се само обекти с `CreatedMixin`/`AuditMixin` (`actors` не). Изричен `created_by_id` се презаписва, освен при source seed/migration.
- `audit_log` е по §2.2 (`occurred_at`, `actor_id`), не по §0.1 (`CreatedMixin`) — разминаване в спецификацията.
- `record_bulk(session, entity_type, ids, changes)` — session е първи аргумент.

## agro-l2v.3 — готово
- `app/models/identity.py` (User/Device/RefreshToken), `app/models/company.py`, миграция `0004` (+ FK `actors.user_id`→users, сийд на company_settings и 3 номерации), `app/services/numbering.py`, `app/services/users.py`, `tests/integration/test_identity_company.py`.
- Спецификацията не дава ЕИК/МОЛ/адрес/подпис/кутия на фирмата → в сийда са `''` (админът ги попълва).
- `take_next` при диапазон: CHECK не допуска `range_end+1`, затова след последния номер `next_number` остава `range_end` и редът става `is_active=false`; следващо извикване → `NumberRangeExhausted` (същата грешка и за спряна номерация).
- `token_hash` добавено в маскираните колони на одита. Тестът за паралелност комитва извън фикстурата и връща `next_number` на `email_ref`.

## Следва
- `l2v.5`…`l2v.13`; `l2v.13` и `zmv.1` нямат блокери.

## agro-1k3 (storage) — частично, НЕ е затворена
- Готово: модел/миграция 0002 `stored_objects`, `app/core/storage.py` (`Storage.save/open/force_upload/retry_local`),
  11 теста с moto. Карта + P13 са зад куката `on_exhausted`; Celery, API retry/hide и FK от имейл/фактури
  чакат l2v.9/zvb.1/br9.1/6p5.1/l2v.6/l2v.10 (подробно в `bd note agro-1k3`).
- `open()` връща стрийм (S3 body или локален файл); извикващият го затваря. Липса и в S3, и локално → `StorageError`.

## Правила, които лесно се нарушават
Не измисляй колони/статуси/маршрути (`bd note <id> "ВЪПРОС: …"`); статуси се менят само в `app/services/`;
пари само `Decimal`, в JSON низове; без `update()`/`delete()` върху одитирани модели; време само през
`app.core.clock`; един commit на заявка (в `get_db`/`session_scope`, не в сервизите).

## agro-l2v.4 — готово
- `app/models/masterdata.py`, `app/domain/emails.py` (+ `normalize_domain`, `is_public_domain`), миграция `0005`, тестове `tests/integration/test_masterdata.py`, `tests/unit/test_emails.py`.
- Валидаторите (`@validates`) нормализират имейл/домейн и отхвърлят публичен домейн (ValueError) на ниво ORM; в базата няма CHECK за публични домейни (спецификацията: валидация в сервиза/админа).
- Частичният уникален индекс е `uq_contacts_email_active` (Index, не UniqueConstraint).
- Тестовата база се вдига от миграциите, затова новите таблици се виждат в тестовете без допълнителна настройка.

## agro-l2v.13 — код готов
- `app/domain/money.py`, `billing.py`, `tests/unit/test_domain_money_dates_billing.py`; domain 100 %.
- Решение на собственика: ден за фактуриране в почивен/празничен ден → най-близкия ПРЕДИШЕН работен ден (`app/domain/dates.py`, библиотеката `holidays` за BG вкл. компенсираните дни; външно API не ползваме — Nager.Date пропуска 28.12.2026). `mi_invoice_grace_working_days` пак е в работни дни. Docs в agro-app са обновени.
- `vat(amount, rate)` — ставката е в проценти (20.00). `next_billing_date` е `>= днес`, `previous_billing_date` е `< днес`.

