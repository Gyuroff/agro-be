"""Графикът на beat. Празен: всяка задача си добавя ред тук в своя тикет (architecture §4)."""

from celery.schedules import crontab, schedule  # noqa: F401  (за редовете на задачите)

beat_schedule: dict[str, dict[str, object]] = {}
