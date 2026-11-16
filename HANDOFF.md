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
- `l2v.6`…`l2v.12`; `zmv.1` няма блокери.

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


## agro-l2v.5 — готово
- `app/models/contracts.py` (Contract, ContractPrice), миграция `0006`, `app/domain/pricing.py` (`pick_price`, `NoPriceForDate` 422),
  `app/services/contracts.py` (`validate_contract`, `price_at`), `app/services/contract_metrics.py`, `tests/integration/test_contracts.py`.
- Добавено в `counterparties`: `default_purchase_payment_mode` (+CHECK) — §6 го изисква, а §5 го няма в таблицата.
- `contracts.status`/`notes` са взети от §6 (колоните стоят след `contract_fund_movements`, но им принадлежат индексите `(side,status)`).
- Освен посочените в тикета CHECK-ове има и „само при страна“ за `order_contact_id`, `purchase_payment_mode`, `tracks_customer_credit` и
  `warehouse_id IS NULL` при sale (§6: „само purchase/sale“, „при sale: NULL“).
- `contract_fund_movements` НЕ е направена (извън обхвата на тикета; в §6 е с разбъркани колони — виж `bd note`).
- `metrics`: `_trip_totals` е заглушка (`{}`), докато няма `trips` (l2v.7/T1.7) — там се дописва GROUP BY заявката.
- В миграцията CHECK върху съществуваща таблица: `op.f(...)` при create, гол суфикс при `drop_constraint` (конвенцията „ck“ иначе удвоява префикса).

## agro-l2v.7 — готово
- `app/domain/plates.py` (100 %), `app/models/trips.py` (ClientRequest, ClientRequestLine, Trip, TripEvent), миграция `0007`,
  `app/services/trips.py` (TripService: `create_planned`, `create_unplanned`, `complete`, `link_unplanned`, `cancel`),
  `contract_metrics._trip_totals` вече е истинска GROUP BY заявка (sale и purchase договори), тестове `tests/integration/test_trips.py`.
- FK НЕ са създадени към още несъществуващи таблици: `client_requests.inbound_email_id/outbound_email_id/agent_run_id`,
  `trips.supplier_report_line_id/invoice_id`, `trip_events.inbound/outbound_email_id` са голи BIGINT. FK се добавят в миграциите
  на l2v.6 (имейли), l2v.8 (справки), l2v.9 (agent_runs), l2v.10 (фактури).
- `create_unplanned` не пише TripEvent: в §8 няма подходящ `kind` за непланиран курс (`linked` и `completed` идват при „Свържи с договор“).
- Цените се четат ПРЕДИ да се пипне курсът — `NoPriceForDate` (422) не оставя курса наполовина сменен.
- `TripService` не прави commit и не пише на заявката/линиите (`ClientRequestLine.trip_id`, `RequestService.finalize()` са в br9.6).

## agro-l2v.9 — готово
- `app/models/cards.py` (ActionCard, AgentRun, PushNotification + StrEnum-и), миграция `0008`, `app/schemas/cards.py`
  (`PAYLOAD_MODELS[CardKind]`, `extra="forbid"`, пари/тонове → низове в JSON), тестове `tests/integration/test_cards.py`,
  `tests/unit/test_card_payloads.py` (примерите са преписани от `docs/api.md` §2).
- FK НЕ са създадени към още несъществуващи таблици: `action_cards.inbound_email_id/outbound_email_id` (l2v.6),
  `supplier_report_id` (l2v.8), `invoice_id/invoice_comparison_id` (l2v.10), `one_shot_deal_id` (`one_shot_deals` няма тикет за модел —
  в тикета е само `action_cards`; таблицата от §10 още липсва).
- `push_notifications.kind` = видовете карти + `send_failed` (P4) + `digest` (P12) — „съществуващите“ в §13 е изведено от `docs/push.md`.
- `agent_runs`/`push_notifications` са в `AUDIT_EXCLUDED_TABLES` (вече бяха), но имат пълен `AuditMixin`.
- Миграция 0008 има `# ruff: noqa: E501` (дългите CHECK списъци).

## agro-6p5.1 — готово
- `app/workers/celery_app.py` (`celery_app`; при `APP_ENV=test` → `task_always_eager`), `beat.py` (празен `beat_schedule`), `audited.py`
  (`@audited_task(actor_code, name=...)`: функцията получава `Session` първа; commit/rollback в `session_scope`, контекст source=worker,
  request_id=id на задачата), `tests/integration/test_audited_task.py`.
- `tests/conftest.py` налага тестовата среда още при импорт (celery_app се създава при импорт, преди фикстурите); без това `.delay()` виси.
- Нови задачи се добавят в `include=[]` на `create_celery()` и в `beat_schedule`.

## agro-6p5.2 — готово
- `app/agents/llm.py` (`get_chat_model`/`get_structured_model`, `StubChatModel`, `StubMissing`, `resolve_model`/`resolve_effort`, `model_id`),
  `graph.py` (`build_extract_graph`: extract → validate → retry ≤1 → fail), `base.py` (`run_agent`), `inputs.py` (`xlsx_to_tsv`, `pdf_text`, `clean_email_text`).
- Stub fixture: `tests/fixtures/llm/<agent>/<sha1>.json`, sha1 от `normalize_input` (роля + текст, whitespace свит). `StubMissing` носи пътя и входа.
- `run_agent(session, agent, (subject_type, id) | None, graph, input, prompt_version, input_summary=None)` НЕ хвърля при грешка в графа
  (иначе rollback изтрива реда): връща `AgentOutcome(run_id, ok, output, error)`; ред в `agent_runs` е ok/error. Графът получава `{"input": …}`,
  резултатът е `state["output"]` (Pydantic). Невалиден изход след 1 повторение → `AgentValidationError` → run `error`.
- Настройки: `APP_LLM_MODEL_<AGENT>` / `APP_LLM_EFFORT_*` в `Settings` (Haiku по подразбиране за classifier и cancellation). `effort` се подава на
  `ChatAnthropic` (поле `reasoning_effort`, псевдоним `effort`). Проверен с реалното API (`scripts/llm_smoke.py`, Haiku и Sonnet). `temperature` НЕ се подава: новите модели връщат 400 „Extra inputs are not permitted“ (тикетът го изискваше).
- Токените се събират с `UsageMetadataCallbackHandler`; stub → 0.

## agro-6p5.3 — готово (без бектеста)
- `app/agents/email_classifier/` (`schemas.py`, `prompts.py` — `email_classifier@1`, `graph.py`: `build_graph()`, `decide()`), тестове `tests/integration/test_email_classifier.py`.
- Вход: `{from_address, subject, body_text (изчистен), attachments: [{name, tsv?}]}`; тялото се реже на 4000 знака, TSV на 30 реда в `render_user`.
- `decide()`: confidence < 0.7 → `kind=other`, `uncertain=True`, причина „несигурен вид“. Картата `unknown_sender` я вдига `mail.process_email` (ha8.5) — payload-ът няма поле за причина (`extra=forbid`), не е измислено.
- Fixtures: случаите са в `tests/fixtures/classifier/cases.json`; `PYTHONPATH=. uv run python scripts/gen_classifier_fixtures.py` ги прави в `tests/fixtures/llm/email_classifier/` (sha1 зависи от промпта — пускай след всяка промяна му).
- Бектест ≥95 % върху D-SAMPLES НЕ е направен: няма реални примери; влиза в 6p5.9 (eval harness).

## agro-6p5.3 — реални примери (2026-11-16)
- Реални писма от двете кутии са изтеглени в `~/agro-samples/raw` (ИЗВЪН git; 678 писма, `index.csv`). Скриптове: `~/agro-samples-tools/`
  (`build_cases.py` анонимизира → `tests/fixtures/classifier/real_cases.json`). Паролите на кутиите НЕ са записани никъде; рано или късно
  трябва да се сменят (споделени в чат).
- Домейнът е торове/фуражи/пелети (КАН, УАН, фуражовози), не зърно. Кутиите се препращат една към друга → дублиране по `Message-ID` (ha8.1).
- В реалната поща НЯМА входящи откази на курсове (`АНУЛИРАНЕ` са изходящи за УНП) → `cancellation` остава само синтетичен.
- Етикетите в `real_cases.json` са мои преценки (`label_verified=false`), собственикът да ги потвърди. Особено: фактури PDF → `mi_invoice`
  (не е ясно кои са от Мега), проформа → `other`, xlsx „Raboten fail“ на клиент → `request`.
- Промптът е `email_classifier@2` (описания по реалните примери). Eval с реален Haiku (`PYTHONPATH=. APP_LLM_PROVIDER=anthropic uv run python
  scripts/eval_classifier.py`): 23/24; единствената грешка — само „ФАКТУРА“+PDF, увереност 0.60 → `other` по правилото.
- Word-HTML писма (напр. „справка КАН“) имат XML боклук (`w:LatentStyles`) и в text/plain → `clean_email_text` не го маха; да се реши в ha8.1/inputs.
- Stub тестовете проверяват само водопровода (отговорът е записаният етикет); точността се мери със `scripts/eval_classifier.py` (влиза в 6p5.9).
- Обновление след отговор на собственика: xlsx на клиент = `request` (потвърдено). Фактурите/проформите от ДОСТАВЧИЦИ НЕ са `mi_invoice` (ползват Мегасофт)
  и не са `other` (проформата води до фактура) → в enum-а няма вид за тях; не е измислен. 5 случая са `pending_spec` (пропускат се в тестовете и точността),
  въпросът е в `bd note agro-6p5.3`. Eval с реален Haiku върху останалите: 19/19.

## agro-l2v.6 — готово
- `app/models/mail.py` (InboundEmail, OutboundEmail, EmailAttachment, MailboxPoll), миграция `0009`, `app/mail/storage.py` (`save_bytes(kind, data, *, suffix=None) → (path, sha256, size)`),
  тестове `tests/integration/test_mail_models.py`, `tests/unit/test_mail_storage.py`.
- Добавени са FK, оставени „голи“ до тук: `client_requests.inbound/outbound_email_id`, `trip_events.*_email_id`, `action_cards.*_email_id`. Тестовете, които са ползвали измислени id-та на писма, вече създават истински `InboundEmail`/`OutboundEmail`.
- inbound ↔ outbound са във взаимна зависимост: FK `inbound_emails.replies_to_outbound_id` е с `use_alter=True` и в миграцията се създава след двете таблици (autogenerate я слагаше в `create_table` → щеше да гърмне).
- `mailbox_polls` вече беше в `AUDIT_EXCLUDED_TABLES`; `email_attachments` е с „точно едно“ CHECK (`<>` върху двете IS NOT NULL).
- ВЪПРОС (в `bd note`): `app/mail/storage.py` (локален диск, `raw_path`) дублира `app/core/storage.py` (S3 първо + `stored_objects`, architecture §11). Направен е по тикета; ha8.1 да реши кой от двата ползва за `.eml`/прикачени.

## Отворено от agro-ha8.1 — трябва да се затвори в agro-ha8.5
- `mail.process_email` още не съществува: `ingest_raw_email` само логва, писмата остават `status=new`.
  Пазачи: `bd note` на agro-ha8.5 и strict-xfail тест `test_process_email_task_is_registered`
  (гърми с XPASS, когато задачата се регистрира — махни маркера и тествай `.delay`).
- Писма, приети преди ha8.5, остават `new` и трябва да се дообработят.
