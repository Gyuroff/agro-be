"""Правила за пуш (docs/push.md §1–§2): шаблони P1–P18, рендер, тихи часове. Чисти функции, без база."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from app.core.clock import SOFIA
from app.models.cards import ActionCard, CardKind, PushKind

NBSP = "\u00a0"
TITLE_SEPARATOR = " · "


def fmt_money(value: Decimal | str) -> str:
    """`32 851,20` (NBSP за хилядите, запетая за стотинките); знакът € го слага шаблонът."""
    text = f"{Decimal(value):,.2f}"
    return text.replace(",", NBSP).replace(".", ",")


def fmt_t(value: Decimal | str) -> str:
    return f"{Decimal(value):,.2f}".replace(",", NBSP).replace(".", ",")


def fmt_day(value: date | str) -> str:
    d = date.fromisoformat(value) if isinstance(value, str) else value
    return f"{d:%d.%m}"


@dataclass(frozen=True)
class Template:
    title: str
    body: str
    urgent: bool = False


# Текстовете са точно от docs/push.md §1. `kind` на send_failed/digest е от PushKind.
TEMPLATES: dict[str, Template] = {
    CardKind.CLIENT_REQUEST.value: Template(
        "Нова заявка от {client}", "{summary}. Провери имейла{to_supplier}."
    ),
    CardKind.SUPPLIER_REPORT.value: Template(
        "Справка от {supplier}", "{rows} курса · {matched} съвпадат · {to_decide} за решение"
    ),
    CardKind.CANCELLATION.value: Template(
        "Отказани {n} курса от {client}", "{day} · {plates}. Потвърди отказа.", urgent=True
    ),
    PushKind.SEND_FAILED.value: Template(
        "Имейлът до {supplier} не е изпратен",
        "Камионите за {period} не са заявени. Опитай пак от таблото.",
        urgent=True,
    ),
    CardKind.UNKNOWN_SENDER.value: Template("Имейл от непознат подател", "{address} · „{subject}“"),
    CardKind.CONTRACT_OVER.value: Template(
        "Договорът с {client} не стига", "№ {number}: не стигат {shortage} т"
    ),
    CardKind.INVOICE_DUE.value: Template("Време за фактура към {client}", "{trips} курса · {total} € с ДДС"),
    CardKind.INVOICE_MISMATCH.value: Template(
        "Разлика във фактура към {client}", "{difference} € спрямо черновата на ERP"
    ),
    CardKind.INVOICE_MISSING.value: Template(
        "Няма фактура от Microinvest за {client}", "Денят за фактуриране беше {billing_date}."
    ),
    CardKind.EXPORT_ERROR.value: Template(
        "Фактура № {number} не е подадена в Microinvest", "Опитай пак от таблото."
    ),
    CardKind.MAILBOX_ALARM.value: Template(
        "Пощата не се проверява", "Последна успешна проверка: {last_ok}. Новите заявки не се виждат."
    ),
    PushKind.DIGEST.value: Template("{n} действия чакат", "{titles}"),
    CardKind.STORAGE_UPLOAD_FAILED.value: Template(
        "Файл не се качва в хранилището", "{filename} · {attempts} опита. Опитай пак от таблото.", urgent=True
    ),
    CardKind.INQUIRY.value: Template("Ново запитване", "{address} · „{subject}“"),
    CardKind.ONE_SHOT_SEND.value: Template(
        "Еднократна сделка — изпрати", "{client} · {qty} т · марж {margin} €/т"
    ),
    CardKind.SUPPLIER_DOC.value: Template("Документ от {supplier}", "{document} · сделка {deal}"),
    CardKind.ONE_SHOT_PAY_SUPPLIER.value: Template("Нареди на {supplier}?", "{amount} € · сделка {deal}"),
    CardKind.SUPPLIER_ADVANCE_LOW.value: Template(
        "Авансът при {supplier} няма да стигне",
        "Недостиг {shortfall} € · салдо {balance} €",
        urgent=True,
    ),
}

DOC_KIND_BG = {"proforma": "проформа", "invoice": "фактура"}


def is_urgent(kind: str) -> bool:
    template = TEMPLATES.get(kind)
    return template is not None and template.urgent


def has_template(kind: str) -> bool:
    return kind in TEMPLATES and kind != PushKind.DIGEST.value


def card_fields(card: ActionCard, *, counterparty_name: str | None, suppliers: list[str]) -> dict[str, str]:
    """Попълва `{…}` от payload-а на картата; име на контрагент и доставчици идват от извикващия."""
    p: dict[str, Any] = card.payload
    name = counterparty_name or ""
    out: dict[str, str] = {"client": name, "supplier": name}
    match card.kind:
        case CardKind.CLIENT_REQUEST.value:
            out["summary"] = card.subtitle or f"{p.get('total_trucks', 0)} камиона"
            out["to_supplier"] = f" към {', '.join(suppliers)}" if suppliers else ""
        case CardKind.SUPPLIER_REPORT.value:
            out.update(
                rows=str(p["total_rows"]),
                matched=str(p["matched"]),
                to_decide=str(p["unplanned"] + p["missing"]),
            )
        case CardKind.CANCELLATION.value:
            out["client"] = name or p["supplier_name"]
            out.update(n=str(len(p["trip_ids"])), day=fmt_day(p["date"]), plates=", ".join(p["plates"]))
        case CardKind.UNKNOWN_SENDER.value | CardKind.INQUIRY.value:
            out.update(address=p["from_address"], subject=p["subject"])
        case CardKind.CONTRACT_OVER.value:
            out.update(number=p["contract_number"], shortage=fmt_t(p["shortage_t"]))
        case CardKind.INVOICE_DUE.value:
            out.update(trips=str(p["trip_count"]), total=fmt_money(p["total"]))
        case CardKind.INVOICE_MISMATCH.value:
            out["difference"] = fmt_money(p["difference"])
        case CardKind.INVOICE_MISSING.value:
            out["billing_date"] = f"{date.fromisoformat(p['billing_date']):%d.%m.%Y}"
        case CardKind.EXPORT_ERROR.value:
            out["number"] = p["invoice_number"]
        case CardKind.MAILBOX_ALARM.value:
            last = p.get("last_ok_at")
            out["last_ok"] = (
                f"{datetime.fromisoformat(last).astimezone(SOFIA):%d.%m %H:%M}" if last else "никога"
            )
        case CardKind.STORAGE_UPLOAD_FAILED.value:
            out.update(filename=p["filename"], attempts=str(p["upload_attempts"]))
        case CardKind.ONE_SHOT_SEND.value:
            out.update(
                client=p["customer_name"],
                qty=fmt_t(p["qty_t"]),
                margin=fmt_money(p["margin_per_t"]),
            )
        case CardKind.SUPPLIER_DOC.value:
            out.update(
                supplier=p["supplier_name"],
                document=DOC_KIND_BG.get(p["doc_kind"]) or str(p["doc_kind"]),
                deal=str(p["deal_id"]),
            )
        case CardKind.ONE_SHOT_PAY_SUPPLIER.value:
            out.update(supplier=p["supplier_name"], amount=fmt_money(p["amount"]), deal=str(p["deal_id"]))
        case CardKind.SUPPLIER_ADVANCE_LOW.value:
            out.update(
                supplier=p["supplier_name"],
                shortfall=fmt_money(p["shortfall"]),
                balance=fmt_money(p["balance"]),
            )
    return out


def render(kind: str, fields: dict[str, str]) -> tuple[str, str]:
    template = TEMPLATES[kind]
    return template.title.format_map(fields), template.body.format_map(fields)


def in_quiet_hours(now: datetime, quiet_from: time, quiet_to: time) -> bool:
    """Прозорецът може да минава през полунощ (21:00–07:00); `now` се гледа по Europe/Sofia."""
    t = now.astimezone(SOFIA).time().replace(tzinfo=None)
    if quiet_from <= quiet_to:
        return quiet_from <= t < quiet_to
    return t >= quiet_from or t < quiet_to


def quiet_end(now: datetime, quiet_from: time, quiet_to: time) -> datetime:
    """Следващият край на тихите часове (`quiet_to` по Sofia), UTC."""
    local = now.astimezone(SOFIA)
    end = datetime.combine(local.date(), quiet_to, tzinfo=SOFIA)
    if end <= local:
        end += timedelta(days=1)
    return end.astimezone(now.tzinfo)


def schedule_for(now: datetime, quiet_from: time, quiet_to: time, *, urgent: bool) -> datetime:
    if urgent or not in_quiet_hours(now, quiet_from, quiet_to):
        return now
    return quiet_end(now, quiet_from, quiet_to)


def is_deferred(scheduled_for: datetime, quiet_to: time, *, urgent: bool) -> bool:
    """Неспешен ред, насрочен точно за края на тихите часове (`schedule_for`) → влиза в обобщението."""
    if urgent:
        return False
    local = scheduled_for.astimezone(SOFIA)
    return local.time().replace(tzinfo=None) == quiet_to
