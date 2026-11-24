"""Админ: изгледи само за четене — одит, курсове, имейли, карти, фактури, сравнения, агенти, проверки
(docs/data-model.md §14)."""

import json
from typing import Any

import anyio
from markupsafe import Markup
from sqladmin import action
from sqladmin.filters import StaticValuesFilter
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.admin.base import ReadOnlyModelView
from app.core.audit_context import AuditSource
from app.db.session import get_sessionmaker
from app.mail.ingest import dispatch_process_email
from app.models.audit import AuditLog
from app.models.cards import ActionCard, AgentName, AgentRun, AgentRunStatus, CardKind, CardStatus
from app.models.enums import AuditAction
from app.models.identity import Actor
from app.models.invoicing import ComparisonResult, Invoice, InvoiceComparison
from app.models.mail import InboundEmail, MailboxPoll, OutboundEmail
from app.models.trips import Trip, TripStatus
from app.services.inbound_mail import reset_for_reprocess


def _choices(enum: Any) -> list[tuple[str, str]]:
    return [(m.value, m.value) for m in enum]


def _json(model: Any, attribute: str) -> Markup:
    return Markup("<pre>{}</pre>").format(
        json.dumps(getattr(model, attribute), ensure_ascii=False, indent=2, sort_keys=True, default=str)
    )


class _LookupFilter(StaticValuesFilter):
    """Филтър, чиито стойности се четат от базата (актьори, видове обекти)."""

    def __init__(self, column: Any, title: str, key: Any, label: Any, cast: Any = str) -> None:
        super().__init__(column, [], title=title)
        self._key = key
        self._label = label
        self._cast = cast

    async def lookups(self, request: Request, model: Any, run_query: Any) -> list[tuple[str, str]]:
        rows = await run_query(select(self._key, self._label).distinct().order_by(self._label))
        return [("__all", "All")] + [(str(key), str(label)) for key, label in rows]

    async def get_filtered_query(self, query: Any, value: Any, model: Any) -> Any:
        if value is None or value in ("", "__all"):
            return query
        return query.filter(self.column == self._cast(value))


class AuditLogAdmin(ReadOnlyModelView, model=AuditLog):
    name = "Одит"
    name_plural = "Одит"
    category = "Само за четене"
    icon = "fa-solid fa-clock-rotate-left"

    column_list = [
        AuditLog.occurred_at,
        AuditLog.actor_id,
        AuditLog.source,
        AuditLog.action,
        AuditLog.entity_type,
        AuditLog.entity_id,
    ]
    column_searchable_list = [AuditLog.entity_type]
    column_filters = [
        StaticValuesFilter(AuditLog.source, _choices(AuditSource), title="Източник"),
        StaticValuesFilter(AuditLog.action, _choices(AuditAction), title="Действие"),
        _LookupFilter(AuditLog.entity_type, "Обект", AuditLog.entity_type, AuditLog.entity_type),
        _LookupFilter(AuditLog.actor_id, "Актьор", Actor.id, Actor.code, cast=int),
    ]
    column_details_list = [
        "id",
        "occurred_at",
        "actor_id",
        "source",
        "request_id",
        "reason",
        "action",
        "entity_type",
        "entity_id",
        "changes",
    ]
    column_formatters_detail = {"changes": lambda m, a: _json(m, a)}
    column_labels = {
        "occurred_at": "Кога",
        "actor_id": "Актьор",
        "source": "Източник",
        "action": "Действие",
        "entity_type": "Обект",
        "entity_id": "№ на обекта",
        "changes": "Промени",
        "reason": "Причина",
    }
    column_default_sort = [("id", True)]


class TripAdmin(ReadOnlyModelView, model=Trip):
    name = "Курс"
    name_plural = "Курсове"
    category = "Само за четене"
    icon = "fa-solid fa-truck"

    column_list = [
        Trip.planned_date,
        Trip.plate_display,
        Trip.status,
        Trip.customer_id,
        Trip.supplier_id,
        Trip.product_id,
        Trip.actual_qty_t,
    ]
    column_searchable_list = [Trip.plate_norm]
    column_filters = [StaticValuesFilter(Trip.status, _choices(TripStatus), title="Статус")]
    column_labels = {
        "planned_date": "Планирана дата",
        "plate_display": "Рег. номер",
        "status": "Статус",
        "customer_id": "Клиент №",
        "supplier_id": "Доставчик №",
        "product_id": "Стока №",
        "actual_qty_t": "Реално, т",
    }


class InboundEmailAdmin(ReadOnlyModelView, model=InboundEmail):
    name = "Входящ имейл"
    name_plural = "Входящи имейли"
    category = "Само за четене"
    icon = "fa-solid fa-envelope-open"

    column_list = [
        InboundEmail.received_at,
        InboundEmail.from_address,
        InboundEmail.subject,
        InboundEmail.kind,
        InboundEmail.status,
        InboundEmail.auth_ok,
    ]
    column_searchable_list = [InboundEmail.from_address, InboundEmail.subject]
    column_details_exclude_list = [InboundEmail.body_html]
    column_labels = {
        "received_at": "Получено",
        "from_address": "От",
        "subject": "Тема",
        "kind": "Вид",
        "status": "Статус",
        "auth_ok": "Проверен подател",
    }

    @action(
        "reprocess",
        "Обработи отново",
        confirmation_message="Писмото ще се върне в „new“ и ще се обработи отново. Сигурни ли сте?",
        add_in_list=True,
        add_in_detail=True,
    )
    async def reprocess(self, request: Request) -> Response:
        pks = [int(p) for p in request.query_params.get("pks", "").split(",") if p]
        await anyio.to_thread.run_sync(reprocess_emails, pks)
        referer = request.headers.get("Referer")
        return RedirectResponse(referer or str(request.url_for("admin:list", identity=self.identity)), 302)


def reprocess_emails(email_ids: list[int]) -> int:
    """Връща писмата в `new` през услугата и СЛЕД commit пуска `mail.process_email`."""
    with get_sessionmaker()() as session:
        ready = reset_for_reprocess(session, email_ids)
        session.commit()
    for email_id in ready:
        dispatch_process_email(email_id)
    return len(ready)


class OutboundEmailAdmin(ReadOnlyModelView, model=OutboundEmail):
    name = "Изходящ имейл"
    name_plural = "Изходящи имейли"
    category = "Само за четене"
    icon = "fa-solid fa-paper-plane"

    column_list = [
        OutboundEmail.created_at,
        OutboundEmail.reference_code,
        OutboundEmail.to_addresses,
        OutboundEmail.subject,
        OutboundEmail.status,
    ]
    column_searchable_list = [OutboundEmail.reference_code]
    column_details_exclude_list = [OutboundEmail.body_html]
    column_labels = {
        "created_at": "Създадено",
        "reference_code": "Референция",
        "to_addresses": "До",
        "subject": "Тема",
        "status": "Статус",
    }


class ActionCardAdmin(ReadOnlyModelView, model=ActionCard):
    name = "Карта"
    name_plural = "Карти"
    category = "Само за четене"
    icon = "fa-solid fa-bell"

    column_list = [
        ActionCard.created_at,
        ActionCard.kind,
        ActionCard.status,
        ActionCard.title,
        ActionCard.resolution,
    ]
    column_filters = [
        StaticValuesFilter(ActionCard.kind, _choices(CardKind), title="Вид"),
        StaticValuesFilter(ActionCard.status, _choices(CardStatus), title="Статус"),
    ]
    column_formatters_detail = {"payload": lambda m, a: _json(m, a)}
    column_labels = {
        "created_at": "Създадена",
        "kind": "Вид",
        "status": "Статус",
        "title": "Заглавие",
        "resolution": "Резолюция",
    }


class InvoiceAdmin(ReadOnlyModelView, model=Invoice):
    name = "Фактура"
    name_plural = "Фактури"
    category = "Само за четене"
    icon = "fa-solid fa-file-invoice"

    column_list = [
        Invoice.billing_date,
        Invoice.counterparty_id,
        Invoice.mode,
        Invoice.status,
        Invoice.number,
        Invoice.total,
        Invoice.mega_export_status,
    ]
    column_searchable_list = [Invoice.number]
    column_labels = {
        "billing_date": "Дата на цикъла",
        "counterparty_id": "Контрагент №",
        "mode": "Режим",
        "status": "Статус",
        "number": "Номер",
        "total": "Сума",
        "mega_export_status": "Експорт към Мега",
    }


class InvoiceComparisonAdmin(ReadOnlyModelView, model=InvoiceComparison):
    name = "Сравнение"
    name_plural = "Сравнения"
    category = "Само за четене"
    icon = "fa-solid fa-code-compare"

    column_list = [
        InvoiceComparison.created_at,
        InvoiceComparison.invoice_id,
        InvoiceComparison.result,
        InvoiceComparison.resolution,
    ]
    column_filters = [
        StaticValuesFilter(InvoiceComparison.result, _choices(ComparisonResult), title="Резултат")
    ]
    column_formatters_detail = {"diff": lambda m, a: _json(m, a)}
    column_labels = {
        "created_at": "Създадено",
        "invoice_id": "Фактура №",
        "result": "Резултат",
        "resolution": "Резолюция",
    }


class AgentRunAdmin(ReadOnlyModelView, model=AgentRun):
    name = "Изпълнение на агент"
    name_plural = "Изпълнения на агенти"
    category = "Само за четене"
    icon = "fa-solid fa-robot"

    column_list = [
        AgentRun.created_at,
        AgentRun.agent,
        AgentRun.status,
        AgentRun.duration_ms,
        AgentRun.error,
    ]
    column_filters = [
        StaticValuesFilter(AgentRun.agent, _choices(AgentName), title="Агент"),
        StaticValuesFilter(AgentRun.status, _choices(AgentRunStatus), title="Статус"),
    ]
    column_formatters_detail = {
        "input_summary": lambda m, a: _json(m, a),
        "output": lambda m, a: _json(m, a),
    }
    column_labels = {
        "created_at": "Създадено",
        "agent": "Агент",
        "status": "Статус",
        "duration_ms": "Време, мс",
        "error": "Грешка",
    }


class MailboxPollAdmin(ReadOnlyModelView, model=MailboxPoll):
    name = "Проверка на пощата"
    name_plural = "Проверки на пощата"
    category = "Само за четене"
    icon = "fa-solid fa-inbox"

    column_list = [MailboxPoll.started_at, MailboxPoll.status, MailboxPoll.fetched_count, MailboxPoll.error]
    column_labels = {
        "started_at": "Започната",
        "status": "Статус",
        "fetched_count": "Изтеглени",
        "error": "Грешка",
    }
