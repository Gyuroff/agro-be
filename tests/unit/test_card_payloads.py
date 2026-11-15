# ruff: noqa: E501
"""Всеки payload модел приема примера от docs/api.md §2 и връща същия JSON (agro-l2v.9)."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.cards import CardKind
from app.schemas.cards import PAYLOAD_MODELS

# Примерите са преписани дословно от docs/api.md §2 („payload по вид“).
EXAMPLES: dict[CardKind, str] = {
    CardKind.CLIENT_REQUEST: """{ "request_id": 31, "inbound_email_id": 812, "to": "orders@peletad.bg", "subject": "Курсове 19.11–25.11 — Агро Трейд ООД", "greeting": "Здравейте,\\nИзпращаме ви курсовете на Агро Трейд ООД за периода 19.11–25.11:", "lines": [{"date":"2026-11-19","plate":"СА 1234 ВХ","driver":"Иван Петров","qty_t":"25.200","uncertain":false}], "total_trucks": 12, "total_qty_t": "302.400", "has_uncertain": true }""",
    CardKind.SUPPLIER_REPORT: """{ "report_id": 9, "received_at": "2026-11-12T09:58:00Z", "total_rows": 12, "total_qty_t": "298.800", "matched": 11, "unplanned": 1, "missing": 1, "preview": [{"plate":"СА 1234 ВХ","product":"Пелети","qty_t":"24.860"}], "substitutes": [{"unplanned_line_id": 55, "missing_line_id": 56, "customer": "Агро Трейд ООД", "date": "2026-11-11", "product": "Пелети", "new_plate": "РВ 2211 ТТ", "new_qty_t": "25.040", "old_plate": "СВ 7890 ТР", "old_planned_qty_t": "25.200"}], "customer_names": ["Агро Трейд ООД"] }""",
    CardKind.CANCELLATION: """{ "trip_ids": [301, 302], "date": "2026-11-13", "plates": ["СВ 7890 ТР","РВ 1122 АВ"], "supplier_name": "Пелет АД", "notify_supplier": true }""",
    CardKind.INVOICE_DUE: """{ "invoice_id": 77, "period_from": "2026-11-04", "period_to": "2026-11-10", "schedule_label": "всеки понеделник", "trip_count": 11, "lines": [{"product":"Пелети","qty_t":"273.760","unit_price":"120.00","amount":"32851.20"}], "vat_rate": "20.00", "vat_amount": "6570.24", "total": "39421.44", "due_date": "2026-11-17" }""",
    CardKind.INVOICE_MISMATCH: """{ "comparison_id": 5, "mi_number": "0000001524", "period_from": "2026-11-04", "period_to": "2026-11-10", "rows": [{"label":"Курсове","erp":"11","mi":"11"},{"label":"Тонаж","erp":"273.760","mi":"273.760"},{"label":"Цена / т","erp":"120.00","mi":"118.00"},{"label":"Без ДДС","erp":"32851.20","mi":"32303.68"}], "difference": "-547.52", "explanation": "Вероятна причина: цената" }""",
    CardKind.INVOICE_MISSING: """{ "invoice_id": 80, "period_from": "2026-11-04", "period_to": "2026-11-10", "subtotal": "15120.00", "billing_date": "2026-11-16" }""",
    CardKind.UNKNOWN_SENDER: """{ "inbound_email_id": 820, "from_address": "office@agro-fermer.bg", "subject": "Заявка за следващата седмица", "suggested_counterparty_id": 12, "suggested_counterparty_name": "Агро Фермер ООД", "suggestion_reason": "domain", "auth_ok": true }""",
    CardKind.CONTRACT_OVER: """{ "contract_id": 4, "contract_number": "2026-021", "product": "Пелети", "remaining_t": "236.400", "requested_t": "302.400", "shortage_t": "66.000" }""",
    CardKind.EXPORT_ERROR: """{ "invoice_id": 81, "invoice_number": "0000001525", "error": "timeout" }""",
    CardKind.MAILBOX_ALARM: """{ "last_ok_at": "2026-11-12T08:43:00Z", "minutes": 75 }""",
    CardKind.STORAGE_UPLOAD_FAILED: """{ "stored_object_id": 901, "filename": "invoice-0000001525.pdf", "upload_attempts": 3, "last_error": "403" }""",
    CardKind.INQUIRY: """{ "inbound_email_id": 830, "from_address": "office@example.bg", "subject": "Запитване", "product_guess": "Урея", "qty_t_guess": "300.000", "suggested_counterparty_id": null }""",
    CardKind.ONE_SHOT_SEND: """{ "deal_id": 12, "customer_name": "Агро Фермер ООД", "supplier_name": "Пелет АД", "product": "Урея", "qty_t": "300.000", "sell_price_per_t": "570.00", "buy_price_per_t": "550.00", "margin_per_t": "20.00", "send_customer_proforma": true, "send_supplier_request": true, "customer_email_preview": { "to": "a@b.bg", "subject": "s", "body": "b" }, "supplier_email_preview": { "to": "c@d.bg", "subject": "s", "body": "b" } }""",
    CardKind.SUPPLIER_DOC: """{ "deal_id": 12, "inbound_email_id": 840, "doc_kind": "proforma", "supplier_name": "Пелет АД", "qty_t": "300.000", "price_per_t": "550.00", "number": "123" }""",
    CardKind.ONE_SHOT_PAY_SUPPLIER: """{ "deal_id": 12, "supplier_name": "Пелет АД", "amount": "165000.00", "supplier_doc_number": "123" }""",
    CardKind.SUPPLIER_ADVANCE_LOW: """{ "purchase_contract_id": 7, "contract_number": "П-2026-007", "supplier_name": "Пелет АД", "balance": "42000.00", "needed": "58000.00", "buffer": "5000.00", "shortfall": "21000.00", "trigger": "request_send" }""",
}


def test_every_kind_has_model_and_example() -> None:
    assert set(PAYLOAD_MODELS) == set(CardKind) == set(EXAMPLES)


@pytest.mark.parametrize("kind", list(CardKind))
def test_example_roundtrips(kind: CardKind) -> None:
    raw: dict[str, Any] = json.loads(EXAMPLES[kind])
    model = PAYLOAD_MODELS[kind].model_validate(raw)
    dumped = model.model_dump(mode="json")
    # низовете за пари/тонове остават низове, без загуба на скала
    for key, value in raw.items():
        if isinstance(value, str) and key.endswith(("_t", "_t_guess", "amount", "total", "balance")):
            assert dumped[key] == value
    assert PAYLOAD_MODELS[kind].model_validate(dumped) == model


def test_unknown_field_rejected() -> None:
    raw = json.loads(EXAMPLES[CardKind.CANCELLATION]) | {"extra": 1}
    with pytest.raises(ValidationError):
        PAYLOAD_MODELS[CardKind.CANCELLATION].model_validate(raw)


def test_float_money_is_not_silently_rounded_in_output() -> None:
    raw = json.loads(EXAMPLES[CardKind.CONTRACT_OVER])
    dumped = PAYLOAD_MODELS[CardKind.CONTRACT_OVER].model_validate(raw).model_dump(mode="json")
    assert isinstance(dumped["remaining_t"], str)
