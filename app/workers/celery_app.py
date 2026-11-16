"""Celery приложение (docs/architecture.md §4). Брокер и backend: APP_REDIS_URL.

Задачите се регистрират в `app/workers/tasks/*.py` и се добавят към `include` от съответния тикет.
"""

from celery import Celery

from app.core.config import Env, get_settings


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery("agro", broker=settings.redis_url, backend=settings.redis_url, include=[])
    app.conf.update(
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        timezone="Europe/Sofia",
        enable_utc=True,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_always_eager=settings.env is Env.TEST,
        task_eager_propagates=True,
    )
    app.config_from_object("app.workers.beat")
    return app


celery_app = create_celery()
