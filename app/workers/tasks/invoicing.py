"""Задачи за фактуриране (architecture §4)."""

from sqlalchemy.orm import Session

from app.core import clock
from app.services import invoice_service, invoicing
from app.workers.audited import audited_task


@audited_task("system:scheduler", name="invoicing.daily_billing")
def daily_billing(session: Session) -> list[int]:
    return invoicing.run_daily_billing(session, clock.today_sofia())


@audited_task("system:scheduler", name=invoice_service.DELIVER_TASK_NAME)
def deliver_invoice(session: Session, invoice_id: int) -> bool:
    """След издаване: PDF и писмо до клиента. Идемпотентна; грешката не пипа номера."""
    return invoice_service.InvoiceService.deliver(session, invoice_id)
