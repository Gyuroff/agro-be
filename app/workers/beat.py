"""Графикът на beat: всяка задача си добавя ред тук в своя тикет (architecture §4)."""

from celery.schedules import crontab, schedule

beat_schedule: dict[str, dict[str, object]] = {
    "mail-poll-inbox": {"task": "mail.poll_inbox", "schedule": schedule(run_every=120)},
    "ops-mailbox-watchdog": {"task": "ops.mailbox_watchdog", "schedule": schedule(run_every=600)},
    "push-dispatch-due": {"task": "push.dispatch_due", "schedule": schedule(run_every=60)},
    "push-morning-digest": {"task": "push.morning_digest", "schedule": crontab(hour=7, minute=0)},
}
