# AGENTS.md — agro-be

Python бекенд на Agro ERP: FastAPI, SQLAlchemy, Alembic, SQLAdmin, Celery, LangGraph.

**Спецификацията не е тук.** Тя е в съседното хранилище `agro-app` (`git@github.com:Gyuroff/agro-app.git`), клонирано в `~/projects/agro-app`. Не копирай документите тук.

## Преди да пипнеш код

1. Задачите са в **локалния** beads на agro-app:
   ```bash
   export BEADS_DIR=~/projects/agro-app/.beads
   bd ready --label repo:be
   bd show <id>
   bd update <id> --claim
   ```
2. Прочети документите, които задачата сочи. Задължително:
   - `../agro-app/docs/data-model.md` — таблици, одит, админ: единственият източник на истина за базата;
   - `../agro-app/docs/architecture.md` — слоеве, версии, конвенции, env, задачи;
   - `../agro-app/docs/best-practices.md` — FastAPI/SQLAlchemy/Celery/LangGraph практики (задължителни);
   - `../agro-app/docs/testing.md` — тестове, golden eval, критерии „замества бизнеса“;
   - `../agro-app/docs/llm.md` — Anthropic модели по агент (Haiku/Sonnet), env `APP_LLM_*`;
   - `../agro-app/docs/api.md` — договорът с приложението;
   - `../agro-app/docs/mvp.md` — бизнес правилата;
   - `../agro-app/docs/push.md` — пуш известия.

## Правила

- **Не измисляй колони, статуси или маршрути.** Ако нещо липсва в спецификацията, спри и напиши `bd note <id> "ВЪПРОС: …"`.
- Всяка промяна на данни минава през одитния контекст (`data-model.md` §2). Ако тест гърми с `MissingAuditContext`, оправи входната точка, а не проверката.
- Статусите се мени само в `app/services/`. Роутерите са тънки.
- Пари: само `Decimal`; в JSON — низове.
- Без `session.execute(update(...))` / `delete(...)` върху одитирани модели.
- Миграции: една на задача, autogenerate + ръчен преглед. `alembic upgrade head` и `alembic downgrade -1` трябва да минават.
- Преди затваряне: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q`, всичко зелено.
- Затваряй с доказателство: файлове, тестове, брой минали тестове.
- Всяка задача завършва с къмит и пуш (`git push`) — след зелената проверка и `bd close`. Съобщението на къмита носи id на задачата, напр. `(agro-xyz)`. Недовършена работа не остава некомитната между сесиите.
- Тайни само в `.env`, никога в git.
