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
- Решение на собственика: ден за фактуриране в почивен/празничен ден → най-близкия ПРЕДИШЕН работен ден (`app/domain/dates.py`, библиотеката `holidays` за BG вкл. компенсираните дни; външно API не ползваме — Nager.Date пропуска 28.12.2026). `mega_invoice_grace_working_days` пак е в работни дни. Docs в agro-app са обновени.
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
- Етикетите в `real_cases.json` са мои преценки (`label_verified=false`), собственикът да ги потвърди. Особено: фактури PDF → `mega_invoice`
  (не е ясно кои са от Мега), проформа → `other`, xlsx „Raboten fail“ на клиент → `request`.
- Промптът е `email_classifier@2` (описания по реалните примери). Eval с реален Haiku (`PYTHONPATH=. APP_LLM_PROVIDER=anthropic uv run python
  scripts/eval_classifier.py`): 23/24; единствената грешка — само „ФАКТУРА“+PDF, увереност 0.60 → `other` по правилото.
- Word-HTML писма (напр. „справка КАН“) имат XML боклук (`w:LatentStyles`) и в text/plain → `clean_email_text` не го маха; да се реши в ha8.1/inputs.
- Stub тестовете проверяват само водопровода (отговорът е записаният етикет); точността се мери със `scripts/eval_classifier.py` (влиза в 6p5.9).
- Обновление след отговор на собственика: xlsx на клиент = `request` (потвърдено). Фактурите/проформите от ДОСТАВЧИЦИ НЕ са `mega_invoice` (ползват Мегасофт)
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

## agro-ha8.3 (съпоставяне на подателя) — готово
- `app/mail/matching.py::match_sender` → `MatchResult`; `tests/integration/test_mail_matching.py` (13 теста).
- Исходящо писмо без `counterparty_id` не печели нишката — пада към контакт/домейн.
- `suggested_counterparty_id`: неактивен контакт с този адрес, неактивен точен домейн, поддомейн или родителски домейн на познат (за „подобен“ спецификацията не дава правило — моя интерпретация). Публичните домейни дават предположение само през неактивен контакт.
- Не записва в `InboundEmail` — това прави `mail.process_email` (agro-ha8.5).

## agro-ha8.5 (mail.process_email) — готово
- `app/services/inbound_mail.py::process` (единствено място, което сменя `inbound_emails.status`), задача `app/workers/tasks/mail.py` (`include` в `celery_app`), `dispatch_process_email` вече вика `.delay`. Тестове: `tests/integration/test_mail_process.py` (13).
- `KIND_HANDLERS: dict[EmailKind, handler]` — тук 6p5.4/6p5.5/6p5.6/6p5.7 регистрират агентите си; без обработчик → `needs_action` + лог. Обработчикът вика само своя запис; върху него е `begin_nested`.
- Несигурен вид (<0.7) → карта `unknown_sender` с подзаглавие „несигурен вид“; `kind` става `other`.
- Грешка (и на класификатора) → `failed` + `error`; `agent_runs` с грешка оцелява. Повторно пускане: админът връща `new` (reprocess — още няма) .
- Картата е създадена директно (`ActionCard`) — няма `CardService` (br9.1); да се мине през него.
- Писма `new` от преди ha8.5 още не са дообработени (няма скрипт).

## agro-ha8.2 (IMAP poll + watchdog) — готово
- `app/mail/sources/imap.py::poll_imap` (BODY.PEEK[], `\Seen` само след успешен `handle`; грешка на писмо не спира другите, то остава UNSEEN),
  `app/services/mailbox.py` (`poll`, `record_poll`, `requeue_stale_new`, `check_watchdog`, `supersede_alarm`), задачи `mail.poll_inbox` (2 мин.)
  и `ops.mailbox_watchdog` (10 мин.) в `beat.py`; тестове `tests/integration/test_mailbox.py` (GreenMail на 3025/3143 — нужен `make up`).
- Неконфигуриран IMAP = ред `mailbox_polls` със status=error („не е конфигуриран“), та watchdog да алармира; без нито един poll → без аларма.
- Резервата за загубен `.delay` гледа `created_at` (не `received_at` — то е от заглавието Date) < 10 мин.
- Картата `mailbox_alarm` е създадена директно (`ActionCard`), не през `CardService` (br9.1 — да се мине през него); `supersede` не слага `resolved_by_id`.
- Неотворено: реален Celery beat/worker не е пускан — само задачите през eager режим/сервизите.

## agro-zmv.1 (вход) — готово
- `app/core/security.py`, `app/services/auth.py`, `app/api/routers/auth.py` (под `/api/v1/auth`), `app/schemas/auth.py`, `current_principal`/`CurrentPrincipal` в `app/api/deps.py`, `tests/integration/test_auth.py` (15 теста).
- Провалите, които трябва да се запазят (брой грешни пароли, reuse detection), се ВРЪЩАТ като `AuthFailure` и роутерът отговаря с `JSONResponse` — `get_db` прави rollback при изключение.
- `current_principal` е async dependency: контекстът се вижда от endpoint-а; преди излизане flush-ва, защото commit-ът на `get_db` е след изтичането на контекста. Нови защитени маршрути ползват `CurrentPrincipal` (`.user`, `.device`).
- Неактивен потребител е навсякъде 401 `user_inactive` (решение на Георги; data-model §3 и api.md §0 са изравнени) — при refresh остава 401 `token_invalid`.
- Паролата се проверява преди `is_active` (не се разкрива дали имейлът съществува); при изтекло заключване броячът започва отначало.

## agro-br9.1 (CardService, действия, /dashboard) — готово
- `app/services/cards.py` (`CardService.create/create_or_get/resolve/perform_action`, регистри `register_action`, `register_on_card_created`), `app/services/dashboard.py`, `app/services/trip_views.py`, `app/schemas/{dashboard,trips}.py`, `app/api/routers/cards.py`, `tests/integration/test_card_service.py`.
- Нови карти се създават САМО през `CardService`; mailbox watchdog и `unknown_sender` вече минават оттам. `create_or_get` връща `(карта, нова)`.
- Куката `on_card_created(card_id)` е на `after_commit` на сесията (не се вика при dedupe/rollback; грешка в нея само се логва). Пуш (zvb.1) се регистрира с `register_on_card_created`.
- Диспечерът: непознато (kind, action) → 404 `not_found` (преди проверката за статус); затворена карта → 409 `card_not_open`. Отговорът има `card: null`, ако действието е затворило картата.
- Handler: `(session, card, body) -> ActionResult(snackbar, undo_token)`; затваря картата с `CardService.resolve`. Общото `hide` е за invoice_missing, contract_over, mailbox_alarm, storage_upload_failed, supplier_advance_low.
- `/dashboard`: „курсове днес“ не включва `cancelled` (спецификацията не казва — мое решение); `route` е long и за курс без клиент е само „Склад X, Склад“ без „→“. `short` още не е описан → същото като long.
- Допълнено по бележката на задачата: `inbound_email_id` в supplier_report payload, `warehouse_name` в substitutes. `invoice_version` за invoice_due → agro-e42.
- Критерият „сийда от T1.12“ не е изпълним (l2v.12 е отворена): тестът на таблото строи данните сам.

## agro-ha8.4 — готово
- `app/mail/smtp.py` (build_message/send), `app/services/outbound.py` (create_draft, queue, send_now, mark_failed, куки `register_before_send` / `register_on_send_failed`), задача `mail.send_outbound` в `app/workers/tasks/mail.py` (актьор `system:scheduler`, без нова миграция), `tests/integration/test_mail_send.py`.
- Кукичето за заявки (`RequestService.finalize`) и пушът `send_failed` (T8.1) още не са закачени — регистрират се през куките. Планирането с `eta=send_after` е за извикващия (`queue` само слага `send_after`).
- Грешка → `failed` + отваряне на свързаните карти (`outbound_email_id`); картата не се създава наново.
- GreenMail: логинът за IMAP е пълният адрес на получателя.

## agro-6p5.4 (request_parser) — готово (без бектеста)
- `app/agents/request_parser/` (`schemas.py`, `prompts.py` — `request_parser@1`, `graph.py`), `app/services/request_intake.py` (`handle`, `pick_contract`; регистрира се в `KIND_HANDLERS[REQUEST]`
  чрез импорт най-долу в `inbound_mail.py`), `inbound_mail.email_agent_input` (бивш `_classifier_input`, вече публичен), тестове `tests/integration/test_request_intake.py` (5).
- Правилата са в кода: невалиден номер → `plate`, дата в миналото → `date`, липсващо `qty_t` → `planned_truck_qty_t` на договора (иначе `default_planned_truck_qty_t`).
- Договор: един активен sale → него; няколко → стеснява по `product_hint` (стока на sale договора), после `warehouse_hint` (склад на supply договора); подсказка без съвпадение не стеснява; не е еднозначно → NULL + „избери договор“ в `subtitle` на картата (payload е `extra=forbid`).
- ОТКЛОНЕНИЕ от тикета: `outbound_emails` draft НЕ се създава при разчитането — `RequestService.send` (br9.6) го прави при „Изпрати“; иначе отхвърлена заявка изгаря АИТ номер.
- Бектест ≥90 % върху D-SAMPLES НЕ е направен (няма реални заявки с разчетени редове) → 6p5.9. Няма stub fixtures в `tests/fixtures/llm/request_parser/`: тестовете ползват `FakeModel` (monkeypatch на `get_chat_model`).
- Реален Sonnet върху текста/xlsx не е пробван (`scripts/llm_smoke.py` е само за водопровода).

## request_parser — калибрация с реален Sonnet (2026-11-16)
- `scripts/eval_request_parser.py` пуска реалния модел върху 6-те `request` случая от `real_cases.json`. Промпт → `request_parser@2`: количества винаги в тонове, липсващ номер = `""`
  (+ `plate` несигурно), две регистрации през „/“ → ремарке. Код: `qty_t ≥ 1000` се приема за кг (÷1000, `qty` несигурно). `plate_raw` в схемата е `""` по подразбиране.
- Отворено: текстовите заявки нямат номера и количество >1 камион — въпрос в `bd note agro-6p5.9`. Реални кутии не са пипани; ползвани са анонимизираните случаи.
- Нови задачи: преглед на реалния имейл след „Изпрати“ (решение/API/екран + бекенд превю) — виж `bd ready`.

## agro-kvk (парсер: седмицата от големия xlsx) — готово
- `app/agents/inputs.py`: `xlsx_to_tsv(path, window=…)` + `date_window(received)` ([получено; +28 дни]). Работният файл на Амета е 1474 реда и расте; седмицата е най-долу.
  Ако в първите 5 реда има колона „Дата на изпълнение“, остават заглавните редове и редовете с дата в прозореца; иначе листът се чете както преди.
  Викa се от `inbound_mail.email_agent_input` (за класификатора и парсера). Проверено с реален Sonnet върху истинско писмо: 27 реда за 11–22.11.
- Открито в данните: в „Място на товарене“ Петя пише „транспорт Ива“ за редовете с НАШ камион (без номер); редовете с рег. № са нейни камиони. Ива връща същия
  файл с попълнено „Място на товарене“ (Разделна/Попово). Връщането на попълнения xlsx при потвърждение НЕ е направено — решение за спецификацията (виж bd).

## agro-2lg (нов модел на заявките) — до 2026-11-16
- Готово (миграции 0010–0013): разфасовки (`warehouses.packagings`, `contract_prices.packaging`), `trips` planned без
  доставчик, поле по ред (packaging/warehouse/purchase_contract/own_transport/truck_type/offer_note/outbound_email_id),
  `counterparties.order_format` и `truck_type`, карта `stock_low`.
- Код: `app/domain/{packaging,order_format}.py`, `app/services/{offers,stock}.py`, `RequestService` (N писма по
  доставчик: compose/preview/send/undo/finalize, `GET …/preview`, `GET …/lines/{id}/offers`), `request_intake.assign_offers`,
  парсерът връща packaging_raw/location_raw/own_transport (PROMPT_VERSION request_parser@3).
- Решения: формат на писмото = настройка на контрагента; праг stock_low <10%; без оферта редът е „избери“, send → 400.
- Файл към клиента (agro-cmf): адаптер по клиент — `app/adapters/client_files/` (`ameta` = Пилко/Амета, колона „Място на
  товарене“), `counterparties.client_file_adapter` (NULL = без файл), `ClientConfirmationService` (превю + send,
  само при заявка `sent`). Нов клиент с файл = нов адаптер. Парсерът връща `source_row` по префикса `[rN]`
  (`request_parser@4`); адаптерът проверява датата на реда и не налучква.
- Остава: `agro-2lg.5` (LLM предложение + decision log), избор на адаптер/формат в админа (`agro-wo5.2`),
  ФЕ `agro-2lg.1–.4`. `xlsx_template` формат на доставчик още не генерира файл. `stock_low` не се затваря сама.
  Писмото към клиента не чака отговорите на доставчиците (няма още статус „потвърдено“ в кода) — само `sent`.
- Спецификацията в `agro-app/docs` е променена и НЕ е комитната (там работи и друга сесия: екран 4/21).

## agro-2lg.7 (API по доставчик) — готово
- `client_request_supplier_drafts` (`ClientRequestSupplierDraft` в `app/models/trips.py`, миграция 0015). `version` е от `AuditMixin` (ORM-ът го вдига при UPDATE) —
  редът се създава при първия PATCH (v1 → flush → промени → v2). `PATCH /requests/{id}/emails/{supplier_id}`; старият `PATCH /requests/{id}` е ПРЕМАХНАТ.
- `GET /requests/{id}`: `groups[]` (+ „Без склад“ с `supplier_id=null`), `customer_name`; няма top-level to/subject/body_intro/supplier_name. Подразбираните тема/поздрав вече не се четат от payload на картата.
- Частично изпращане: `send` праща само редовете с доставчик, които не са в `queued`/`sent` писмо (`RequestService.sent_line_ids`, по статуса на `outbound_emails`); заявката е `queued` и картата `done` само когато няма ред без склад, иначе `draft`/`open`. Изпратен ред не се редактира/маха (409).
- `undo_token` вече носи `outbound_email_ids` (на това „Изпрати“); undo връща само тях. Стари токени (с `outbound_email_id`) не важат.
- `preview.emails[]` няма version/line_ids (не са поискани).

## agro-sw1 (call_phone) + GET /emails/{id} от agro-br9.9 — готово
- `app/api/routers/emails.py`, `app/schemas/emails.py`, `app/services/email_views.py` (`get_email`, `sender_phone`), `tests/integration/test_email_api.py` (4).
- `call_phone`: `contact.phone` на писмото, иначе `counterparty.phone`, иначе null. Само входящи писма; вариантът за изходящо писмо с обект `reply` (api.md §3) НЕ е направен.
- Мои интерпретации (спецификацията не ги дава): `match_label` = „<име> · контакт|домейн|нишка|код в темата|ръчно“ по `match_method`; `rows` на xlsx се смята при четене
  (няма колона; празните редове и заглавният не се броят; нечетим файл → null); `uncertain` се строи от `uncertain_fields`/`uncertain_note` на редовете на заявката
  („Шофьор на СВ 7890 ТР“); `agent_notes` = `client_requests.agent_notes` (за справки още няма).
- `GET /attachments/{id}` готов (FileResponse от `storage_path`, липсващ файл → 404). agro-br9.9 затворена; `reply` и agent_notes за справки са във нови задачи (виж bd).

## agro-e42 — готово
- `InvoiceDuePayload.invoice_version` (app/schemas/cards.py), примерът в tests/unit/test_card_payloads.py е по api.md §2. `hide` за `mailbox_alarm` вече беше в `HIDE_KINDS`; добавен тест. Карти `invoice_due` още никой не създава (идва с agro-66f.1) — там трябва да се подава версията. Бутонът `issue` с `invoice_version` е на agro-66f.2.

## agro-iat (нови видове писмо) — готово, затворена
- `make check` зелено (474 теста). Миграция `0016` (CHECK `kind` на `action_cards` и `push_notifications` + `payment_question`); колоните `kind/doc_kind/reply_status/replies_to_outbound_id` и `payment_reply` бяха от `l2v.6`.
- `ClassifierOutput` (+`doc_kind`, `reply_status`; EmailKind/DocKind/ReplyStatus са от `app/models/mail.py`), `decide()` изхвърля чуждите полета, `_validate` връща грешка при липсващо `doc_kind`/`reply_status` (→ 1 повторение). `email_classifier@3`; `inbound_mail._classify` записва двете полета.
- `app/services/payment_questions.py`: обработчик → карта `payment_question` (`invoice_number` с regex от темата/тялото; няма → null), действия `reply_paid`/`reply_will_pay`/`ignore`. Отговорът е `payment_reply` в нишката, `undo_token` = `make_undo_token` (същият `RequestService.undo`; след „Отмени“ се ползва същото писмо). `attachment_ids` ≠ [] → 400 (не е направено). Регистрира се при импорт от `inbound_mail` (worker) и от `routers/cards.py` (API).
- `reply` без карта: `_handle_reply` → `processed`; етикетът се смята при четене (agro-nfi).
- `inquiry` и `supplier_doc` още нямат обработчик (→ `needs_action` + лог): `one_shot_deals` няма модел; `to_one_shot`/`confirm_link` чакат.
- Eval с реален Haiku (`scripts/eval_classifier.py`, вече проверява и `doc_kind`/`reply_status`): 29/30, 30/30, 29/30 в три пуска. Единственото колебание е `real_11` (две „Проф ф-ра“, GDPR бележка; видът е верен, увереност 0.60–0.70 → под прага става `other`). Критерият „≥95%“ е изпълнен (29/30 = 96,7% във всеки пуск); `real_11` е единственото колебание; `real_12` е вече `reply`/confirmed. Синтетичният `mega_invoice` е променен да е ясно от Мега (иначе е неразличим от `supplier_doc`).

## agro-6xw: mi_* / microinvest_* → mega_*
- Миграция `0017_rename_mi_to_mega.py` (нагоре/надолу/`alembic check` минават): колони `mega_code`, `mega_invoice_grace_working_days`, constraint-и, CHECK за `inbound_emails.kind`, `agent_runs.agent`, `action_cards.resolution`, `stored_objects.kind` + UPDATE на съществуващите редове, `actors.code='agent:mega_invoice_parser'`, `dedupe_key mi_missing:` → `mega_missing:`.
- Env: `APP_MEGA_TEST_EXPORT_DIR` / `APP_MEGA_PROD_EXPORT_DIR`. API: `mega_number`, `rows[].mega`, resolution `mega_correct`. Деплой в един ден с agro-app (Flutter).
- Stub fixtures на класификатора са прегенерирани (sha1 зависи от промпта). Eval: 30/30.

## agro-wo5.2 (админ изгледи 1) — готово
- `app/admin/views/identity.py` (потребители, устройства, фирма, номерация), `masterdata.py` (контрагенти, домейни, контакти, стоки, складове), `ALL_VIEWS`; тестове `tests/integration/test_admin_views.py` (9).
- Добавени `relationship` към `Device.user` и `counterparty` на домейн/контакт/склад (само ORM, без миграция) — SQLAdmin иска релации за падащи менюта и колони „Контрагент“/„Потребител“.
- `AuditedModelView.column_details_list="__all__"` връща и `password_hash`/`push_token` — изгледите за потребители и устройства имат изричен списък без тях. Нови изгледи със секрети правят същото.
- Устройства: `can_edit=False` (спецификацията казва „✓ само revoked_at чрез действие“; действието „Отнеми“ е единствената промяна). Контрагент `erp` без договор с цена се отхвърля само при РЕДАКЦИЯ (нов контрагент няма как да има договор).
- ЕИК (9/13 цифри) и ДДС (`BG123…`) се валидират в `on_model_change`; формат на ДДС е моя интерпретация.
- Тестовете комитват за реално (админът ползва свои връзки); фикстурата `web` чисти устройства/токени, иначе чупят `test_auth` (избира всички `RefreshToken`).

## agro-w5h (funds в GET /contracts/{id}) — готово
- `FundsDTO` в `app/schemas/contracts.py`, `_funds/_needed/_buffer` в `app/services/contract_views.py`, 3 теста в `tests/integration/test_contracts_api.py`.
- Решения на собственика: needed = Σ(planned_qty_t × цена по разфасовка) за planned курсове на договора; reserved = needed; available = balance − reserved; buffer: percent от needed / amount / NULL→0.00.
- Мои допускания: ред без разфасовка/без цена за нея → цената на първата налична (bulk преди bagged); при held_by=us `reserved`=0.00, `available`=balance (спецификацията не дава правило).
- Примерът в `docs/api.md` §6 (reserved 18000/available 24000 при needed 58000) не съвпада с правилото reserved = needed — да се поправи в agro-app.
- Няма `supplier_advance_low` изчисление в бекенда още; когато се прави, да ползва `_needed/_buffer`.
