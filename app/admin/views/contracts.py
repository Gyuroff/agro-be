"""Админ: договори, цени, разходи, категории, резултат преди старта (docs/data-model.md §14)."""

from datetime import date
from typing import Any

import anyio
from sqladmin.filters import StaticValuesFilter
from starlette.requests import Request

from app.admin.base import AuditedModelView
from app.core.errors import DomainError
from app.db.session import get_sessionmaker
from app.models.contracts import Contract, ContractPrice
from app.models.result import Expense, ExpenseCategory, MonthlyResultOpening
from app.services.contracts import validate_contract


def _ref_id(value: Any) -> int | None:
    """Връзка от формата: обект на модела (или вече id) → id."""
    if value is None or value == "":
        return None
    return int(getattr(value, "id", value))


def _check_contract(data: dict, contract_id: int | None) -> None:
    """Валидациите на сийда (`validate_contract`) върху преходен `Contract` от данните на формата."""
    candidate = Contract(
        side=data.get("side"),
        counterparty_id=_ref_id(data.get("counterparty")),
        product_id=_ref_id(data.get("product")),
        supply_contract_id=_ref_id(data.get("supply_contract")),
        order_contact_id=_ref_id(data.get("order_contact")),
        billing_rrule=(data.get("billing_rrule") or None),
    )
    candidate.id = contract_id  # pyright: ignore[reportAttributeAccessIssue]
    with get_sessionmaker()() as session:
        try:
            validate_contract(session, candidate)
        except DomainError as exc:
            raise ValueError(exc.message_bg) from exc
        finally:
            session.expunge_all()


class ContractAdmin(AuditedModelView, model=Contract):
    name = "Договор"
    name_plural = "Договори"
    category = "Договори и цени"
    icon = "fa-solid fa-file-contract"

    column_list = [
        Contract.number,
        Contract.side,
        Contract.counterparty,
        Contract.product,
        Contract.contracted_qty_t,
        Contract.valid_from,
        Contract.valid_to,
        Contract.status,
    ]
    column_searchable_list = [Contract.number]
    column_filters = [
        StaticValuesFilter(Contract.side, [("sale", "Продажба"), ("purchase", "Покупка")], title="Вид"),
        StaticValuesFilter(
            Contract.status,
            [("active", "Активен"), ("closed", "Приключен")],
            title="Статус",
        ),
    ]
    column_labels = {
        "number": "Номер",
        "side": "Вид",
        "counterparty": "Контрагент",
        "product": "Стока",
        "warehouse": "Склад",
        "supply_contract": "Насрещен договор",
        "order_contact": "Контакт за поръчки",
        "contracted_qty_t": "Договорено количество (т)",
        "valid_from": "Валиден от",
        "valid_to": "Валиден до",
        "status": "Статус",
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        contract_id = None if is_created else model.id
        await anyio.to_thread.run_sync(_check_contract, data, contract_id)


class ContractPriceAdmin(AuditedModelView, model=ContractPrice):
    name = "Цена по договор"
    name_plural = "Цени по договор"
    category = "Договори и цени"
    icon = "fa-solid fa-tag"

    can_edit = False  # грешна цена се поправя с нов ред — историята е важна за маржа
    column_list = [ContractPrice.contract, ContractPrice.valid_from, ContractPrice.price_per_t]
    column_searchable_list = ["contract.number"]
    column_labels = {
        "contract": "Договор",
        "packaging": "Разфасовка",
        "valid_from": "Валидна от",
        "price_per_t": "Цена за тон",
    }


class ExpenseCategoryAdmin(AuditedModelView, model=ExpenseCategory):
    name = "Категория разходи"
    name_plural = "Категории разходи"
    category = "Резултат"
    icon = "fa-solid fa-folder"

    column_list = [ExpenseCategory.name, ExpenseCategory.sort_order, ExpenseCategory.is_active]
    column_labels = {"name": "Име", "sort_order": "Ред", "is_active": "Активна"}


class ExpenseAdmin(AuditedModelView, model=Expense):
    name = "Прогнозен разход"
    name_plural = "Прогнозни разходи"
    category = "Резултат"
    icon = "fa-solid fa-money-bill-wave"

    can_delete = True  # прогноза, не документ — изтриването пак влиза в audit_log
    column_list = [
        Expense.category,
        Expense.kind,
        Expense.amount,
        Expense.start_month,
        Expense.end_month,
        Expense.description,
    ]
    column_filters = [
        StaticValuesFilter(Expense.kind, [("monthly", "Месечен"), ("one_off", "Еднократен")], title="Вид")
    ]
    column_labels = {
        "category": "Категория",
        "kind": "Вид",
        "amount": "Сума",
        "start_month": "От месец",
        "end_month": "До месец",
        "description": "Описание",
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        start = data.get("start_month")
        end = data.get("end_month")
        if isinstance(start, date) and start.day != 1:
            raise ValueError("Началният месец трябва да е първо число.")
        if isinstance(end, date) and end.day != 1:
            raise ValueError("Крайният месец трябва да е първо число.")
        if data.get("kind") == "one_off" and end != start:
            raise ValueError("Еднократният разход е за един месец: краят трябва да съвпада с началото.")


class MonthlyResultOpeningAdmin(AuditedModelView, model=MonthlyResultOpening):
    name = "Резултат преди старта"
    name_plural = "Резултат преди старта"
    category = "Резултат"
    icon = "fa-solid fa-chart-line"

    column_list = [
        MonthlyResultOpening.month,
        MonthlyResultOpening.gross_margin,
        MonthlyResultOpening.expenses,
    ]
    column_labels = {
        "month": "Месец",
        "gross_margin": "Брутен марж",
        "expenses": "Разходи",
        "note": "Бележка",
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        month = data.get("month")
        if isinstance(month, date) and month.day != 1:
            raise ValueError("Месецът трябва да е първо число.")
