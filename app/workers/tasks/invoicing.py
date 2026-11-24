"""Задачи за фактуриране (architecture §4)."""

from sqlalchemy.orm import Session

from app.core import clock
from app.services import invoicing
from app.workers.audited import audited_task


@audited_task("system:scheduler", name="invoicing.daily_billing")
def daily_billing(session: Session) -> list[int]:
    return invoicing.run_daily_billing(session, clock.today_sofia())
