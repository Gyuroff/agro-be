"""Графикът на beat: всяка задача си добавя ред тук в своя тикет (architecture §4)."""

from celery.schedules import crontab, schedule  # noqa: F401  (за редовете на задачите)

beat_schedule: dict[str, dict[str, object]] = {
    "mail-poll-inbox": {"task": "mail.poll_inbox", "schedule": schedule(run_every=120)},
    "ops-mailbox-watchdog": {"task": "ops.mailbox_watchdog", "schedule": schedule(run_every=600)},
}
