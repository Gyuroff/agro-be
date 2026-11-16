"""Служебни задачи (architecture §4)."""

from sqlalchemy.orm import Session

from app.services import mailbox
from app.workers.audited import audited_task


@audited_task("system:scheduler", name="ops.mailbox_watchdog")
def mailbox_watchdog(session: Session) -> bool:
    return mailbox.check_watchdog(session)
