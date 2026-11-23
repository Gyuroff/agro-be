"""Админ: контрагенти, домейни, контакти, стоки, складове (docs/data-model.md §14)."""

import re
from typing import Any

import anyio
from sqladmin.filters import BooleanFilter, StaticValuesFilter
from sqlalchemy import func, select
from starlette.requests import Request
from wtforms import SelectField

from app.adapters.client_files import adapter_choices
from app.admin.base import AuditedModelView
from app.db.session import get_sessionmaker
from app.models.contracts import Contract, ContractPrice
from app.models.masterdata import (
    Contact,
    Counterparty,
    CounterpartyDomain,
    InvoicingMode,
    OrderFormat,
    Product,
    Warehouse,
)

_EIK = re.compile(r"^[0-9]{9}([0-9]{4})?$")
_VAT = re.compile(r"^[A-Z]{2}[0-9A-Z]{2,12}$")

ORDER_FORMAT_LABELS = {
    OrderFormat.PER_TRUCK: "По камиони (дата · камион · шофьор · количество)",
    OrderFormat.PER_DAY: "По дни (вид автомобил · количество · общо)",
    OrderFormat.PER_PACKAGING_COUNT: "Брой курсове по опаковка",
    OrderFormat.FREE_SCHEDULE: "Свободен график по дни",
    OrderFormat.XLSX_TEMPLATE: "Кратко писмо + xlsx по шаблон",
}


class CounterpartyAdmin(AuditedModelView, model=Counterparty):
    name = "Контрагент"
    name_plural = "Контрагенти"
    category = "Контрагенти и стоки"
    icon = "fa-solid fa-handshake"

    column_list = [
        Counterparty.name,
        Counterparty.eik,
        Counterparty.invoicing_mode,
        Counterparty.mega_code,
        Counterparty.is_active,
    ]
    column_searchable_list = [Counterparty.name, Counterparty.eik]
    column_filters = [
        StaticValuesFilter(
            Counterparty.invoicing_mode,
            [("shadow", "Сянка"), ("erp", "ERP")],
            title="Режим на фактуриране",
        ),
        BooleanFilter(Counterparty.is_active, title="Активен"),
    ]
    column_labels = {
        "name": "Име",
        "legal_name": "Юридическо име",
        "eik": "ЕИК",
        "vat_number": "ДДС номер",
        "is_vat_registered": "Регистриран по ДДС",
        "country_code": "Държава",
        "address_city": "Град",
        "address_line": "Адрес",
        "mol": "МОЛ",
        "mega_code": "Код в Мегасофт",
        "invoice_email": "Имейл за фактури",
        "invoicing_mode": "Режим на фактуриране",
        "merge_contracts_in_invoice": "Обединява договорите във фактура",
        "default_billing_mode": "Начин на фактуриране",
        "default_billing_rrule": "График на фактуриране (RRULE)",
        "default_payment_terms_days": "Дни за плащане",
        "default_purchase_payment_mode": "Начин на плащане при покупка",
        "order_format": "Формат на писмото до доставчика",
        "client_file_adapter": "Файл към клиента",
        "truck_type": "Вид камион",
        "phone": "Телефон",
        "notes": "Бележки",
        "is_active": "Активен",
    }
    form_overrides = {"order_format": SelectField, "client_file_adapter": SelectField}
    form_args = {
        "order_format": {
            "choices": [(f.value, ORDER_FORMAT_LABELS[f]) for f in OrderFormat],
            "coerce": str,
        },
        "client_file_adapter": {
            "choices": [("", "— без файл —"), *adapter_choices()],
            "coerce": str,
            "validate_choice": True,
        },
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        eik = str(data.get("eik") or "").strip()
        if not _EIK.match(eik):
            raise ValueError("ЕИК трябва да е 9 или 13 цифри.")
        vat_number = str(data.get("vat_number") or "").strip().upper() or None
        if vat_number and not _VAT.match(vat_number):
            raise ValueError("ДДС номерът трябва да е във вид „BG123456789“.")
        data["vat_number"] = vat_number
        data["client_file_adapter"] = data.get("client_file_adapter") or None
        data["mega_code"] = str(data.get("mega_code") or "").strip() or None
        data["invoice_email"] = str(data.get("invoice_email") or "").strip().lower() or None

        if data.get("invoicing_mode") == InvoicingMode.ERP.value:
            if not data["mega_code"]:
                raise ValueError("В режим „erp“ кодът в Мегасофт е задължителен.")
            if not data["invoice_email"]:
                raise ValueError("В режим „erp“ имейлът за фактури е задължителен.")
            # нов контрагент още няма договори — проверката е при редакция
            if not is_created and not await anyio.to_thread.run_sync(_has_priced_contract, model.id):
                raise ValueError("В режим „erp“ е нужен поне един договор с цена.")


def _has_priced_contract(counterparty_id: int) -> bool:
    with get_sessionmaker()() as session:
        count = session.scalar(
            select(func.count(ContractPrice.id))
            .join(Contract, Contract.id == ContractPrice.contract_id)
            .where(Contract.counterparty_id == counterparty_id)
        )
        return bool(count)


class DomainAdmin(AuditedModelView, model=CounterpartyDomain):
    name = "Домейн"
    name_plural = "Домейни"
    category = "Контрагенти и стоки"
    icon = "fa-solid fa-globe"

    can_delete = True
    column_list = [CounterpartyDomain.counterparty, CounterpartyDomain.domain, CounterpartyDomain.is_active]
    column_searchable_list = [CounterpartyDomain.domain]
    column_labels = {"counterparty": "Контрагент", "domain": "Домейн", "is_active": "Активен"}
    # публичните домейни се отхвърлят от валидатора на модела (ValueError → грешка във формата)


class ContactAdmin(AuditedModelView, model=Contact):
    name = "Контакт"
    name_plural = "Контакти"
    category = "Контрагенти и стоки"
    icon = "fa-solid fa-address-book"

    column_list = [
        Contact.counterparty,
        Contact.full_name,
        Contact.email,
        Contact.phone,
        Contact.sends_requests,
        Contact.sends_reports,
        Contact.receives_orders,
        Contact.receives_invoices,
        Contact.source,
    ]
    column_searchable_list = [Contact.email, Contact.full_name]
    column_labels = {
        "counterparty": "Контрагент",
        "full_name": "Име",
        "email": "Имейл",
        "phone": "Телефон",
        "sends_requests": "Изпраща заявки",
        "sends_reports": "Изпраща справки",
        "receives_orders": "Получава поръчки",
        "receives_invoices": "Получава фактури",
        "source": "Произход",
        "is_active": "Активен",
    }


class ProductAdmin(AuditedModelView, model=Product):
    name = "Стока"
    name_plural = "Стоки"
    category = "Контрагенти и стоки"
    icon = "fa-solid fa-box"

    column_list = [Product.name, Product.mega_code, Product.vat_rate, Product.is_active]
    column_searchable_list = [Product.name]
    column_labels = {
        "name": "Име",
        "unit": "Мерна единица",
        "mega_code": "Код в Мегасофт",
        "vat_rate": "ДДС, %",
        "is_active": "Активна",
    }


class WarehouseAdmin(AuditedModelView, model=Warehouse):
    name = "Склад"
    name_plural = "Складове"
    category = "Контрагенти и стоки"
    icon = "fa-solid fa-warehouse"

    column_list = [Warehouse.counterparty, Warehouse.name, Warehouse.is_active]
    column_searchable_list = [Warehouse.name]
    column_labels = {
        "counterparty": "Контрагент",
        "name": "Име",
        "address": "Адрес",
        "packagings": "Разфасовки",
        "is_active": "Активен",
    }
