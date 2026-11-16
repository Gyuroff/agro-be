"""`@audited_task` — Celery задача с транзакция и одитен контекст (best-practices, architecture §3.2/§4).

Декорираната функция получава `Session` като първи аргумент. Commit е тук (един на задача),
rollback при изключение. Без актьор няма запис: непознат `actor_code` гърми.
"""

from collections.abc import Callable
from functools import wraps
from typing import Any, Concatenate, cast

from celery import Task
from sqlalchemy.orm import Session

from app.core.audit_context import AuditSource, actor_ctx_by_code, audit_context
from app.db import session as db_session
from app.workers.celery_app import celery_app


def audited_task[**P](
    actor_code: str, *, name: str | None = None, **celery_options: Any
) -> Callable[[Callable[Concatenate[Session, P], object]], Task]:
    def decorator(fn: Callable[Concatenate[Session, P], object]) -> Task:
        task_name = name or f"{fn.__module__}.{fn.__name__}"

        @celery_app.task(bind=True, name=task_name, **celery_options)
        @wraps(fn)
        def run(self: Task, *args: P.args, **kwargs: P.kwargs) -> object:
            with db_session.session_scope() as session:
                ctx = actor_ctx_by_code(
                    actor_code,
                    AuditSource.WORKER,
                    request_id=self.request.id,
                    reason=f"task:{task_name}",
                    session=session,
                )
                with audit_context(ctx):
                    return fn(session, *args, **kwargs)

        return cast(Task, run)

    return decorator
