"""Агент „фактура от Мега“ (agro-6p5.7): писмо `mega_invoice` → `mega_invoices` (stub LLM, синтетични PDF)."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.models.cards import ActionCard, AgentRun, CardKind
from app.models.invoicing import MegaInvoice
from app.models.mail import EmailAttachment, InboundEmail, InboundStatus
from app.models.masterdata import Counterparty
from app.services import mega_invoice_intake
from tests.integration.test_report_intake import FakeModel
from tests.pdf_factory import make_pdf

pytestmark = pytest.mark.mail


def _line(desc: str, qty: str, price: str, amount: str, vat: str = "20") -> dict[str, str]:
    return {"description": desc, "qty_t": qty, "unit_price": price, "amount": amount, "vat_rate": vat}


# Три фактури с различна форма: един ред; два реда (смяна на цената); ред с ДДС 9%.
CASES: list[dict[str, Any]] = [
    {
        "number": "0000001524",
        "issue_date": "2026-11-05",
        "recipient_eik": "111111111",
        "lines": [_line("Pelets", "25.000", "120.00", "3000.00")],
        "subtotal": "3000.00",
        "vat_amount": "600.00",
        "total": "3600.00",
    },
    {
        "number": "0000001525",
        "issue_date": "2026-11-12",
        "recipient_eik": "BG111111111",
        "lines": [
            _line("Pelets", "12.500", "118.00", "1475.00"),
            _line("Pelets", "12.500", "120.00", "1500.00"),
        ],
        "subtotal": "2975.00",
        "vat_amount": "595.00",
        "total": "3570.00",
    },
    {
        "number": "0000001526",
        "issue_date": "2026-11-20",
        "recipient_eik": "999999999",
        "lines": [_line("Briquettes", "10.333", "97.50", "1007.47", "9")],
        "subtotal": "1007.47",
        "vat_amount": "90.67",
        "total": "1098.14",
    },
]


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    def set_outputs(outputs: dict[str, Any]) -> None:
        monkeypatch.setattr("app.agents.llm.get_chat_model", lambda agent: FakeModel(agent, outputs))

    return set_outputs


def _mail(
    db: Session, tmp_path: Path, name: str, *, pdf: bool = True, text: list[str] | None = None
) -> InboundEmail:
    client = Counterparty(name="Агро Трейд", legal_name="Агро Трейд ООД", eik="111111111")
    db.add(client)
    db.flush()
    mail = InboundEmail(
        message_id=f"<{name}@x>",
        from_address="accountant@firm.bg",
        subject="Фактура",
        body_text="Прикачена е фактурата",
        received_at=clock.now(),
        raw_path="/tmp/x.eml",
        auth_ok=True,
        kind="mega_invoice",
    )
    db.add(mail)
    db.flush()
    if pdf:
        path = make_pdf(
            tmp_path / f"{name}.pdf", text if text is not None else [f"Faktura {name}", "Pelets 25 t"]
        )
        db.add(
            EmailAttachment(
                inbound_email_id=mail.id,
                filename=f"{name}.pdf",
                content_type="application/pdf",
                size_bytes=path.stat().st_size,
                sha256=name,
                storage_path=str(path),
            )
        )
        db.flush()
    return mail


def _unknown_card(db: Session, mail: InboundEmail) -> ActionCard:
    return db.scalars(select(ActionCard).where(ActionCard.inbound_email_id == mail.id)).one()


@pytest.mark.parametrize("case", CASES, ids=[c["number"] for c in CASES])
def test_fields_saved(
    db_session: Session,
    audit_ctx: AuditContext,
    tmp_path: Path,
    stub: Callable[[dict[str, Any]], None],
    case: dict[str, Any],
) -> None:
    seen: list[Any] = []

    def respond(messages: Any) -> dict[str, Any]:
        seen.append(messages)
        return case

    stub({"mega_invoice_parser": respond})
    mail = _mail(db_session, tmp_path, "inv" + case["number"])

    assert mega_invoice_intake.handle(db_session, mail) is InboundStatus.PROCESSED

    assert "Faktura inv" + case["number"] in str(seen[0])  # агентът получава текста на PDF
    inv = db_session.scalars(select(MegaInvoice)).one()
    assert inv.number == case["number"] and inv.source == "email_copy"
    assert inv.issue_date is not None and inv.issue_date.isoformat() == case["issue_date"]
    assert (inv.subtotal, inv.vat_amount, inv.total) == tuple(
        Decimal(case[k]) for k in ("subtotal", "vat_amount", "total")
    )
    assert inv.lines == [
        {k: v if k == "description" else str(Decimal(v)) for k, v in ln.items()} for ln in case["lines"]
    ]
    assert inv.inbound_email_id == mail.id and inv.attachment_id is not None
    assert inv.agent_run_id == db_session.scalars(select(AgentRun.id)).one()
    known = case["recipient_eik"].removeprefix("BG") == "111111111"
    assert (inv.counterparty_id is not None) is known
    assert db_session.scalars(select(ActionCard)).all() == []


def test_arithmetic_error_is_uncertain(
    db_session: Session, audit_ctx: AuditContext, tmp_path: Path, stub: Callable[[dict[str, Any]], None]
) -> None:
    stub({"mega_invoice_parser": {**CASES[0], "subtotal": "3100.00", "total": "3720.00"}})
    mail = _mail(db_session, tmp_path, "bad")

    assert mega_invoice_intake.handle(db_session, mail) is InboundStatus.NEEDS_ACTION

    assert db_session.scalars(select(MegaInvoice)).all() == []
    card = _unknown_card(db_session, mail)
    assert card.kind == CardKind.UNKNOWN_SENDER.value
    assert "неразчетена фактура" in (card.subtitle or "")


def test_vat_mismatch_is_uncertain(
    db_session: Session, audit_ctx: AuditContext, tmp_path: Path, stub: Callable[[dict[str, Any]], None]
) -> None:
    stub({"mega_invoice_parser": {**CASES[0], "vat_amount": "500.00", "total": "3500.00"}})
    mail = _mail(db_session, tmp_path, "vat")
    assert mega_invoice_intake.handle(db_session, mail) is InboundStatus.NEEDS_ACTION
    assert "ДДС" in (_unknown_card(db_session, mail).subtitle or "")


def test_cent_tolerance_is_ok(
    db_session: Session, audit_ctx: AuditContext, tmp_path: Path, stub: Callable[[dict[str, Any]], None]
) -> None:
    stub({"mega_invoice_parser": {**CASES[0], "subtotal": "3000.01", "total": "3600.01"}})
    mail = _mail(db_session, tmp_path, "cent")
    assert mega_invoice_intake.handle(db_session, mail) is InboundStatus.PROCESSED


def test_no_pdf_and_empty_text_do_not_call_llm(
    db_session: Session, audit_ctx: AuditContext, tmp_path: Path, stub: Callable[[dict[str, Any]], None]
) -> None:
    stub({})  # KeyError, ако агентът бъде извикан
    no_pdf = _mail(db_session, tmp_path, "nopdf", pdf=False)
    assert mega_invoice_intake.handle(db_session, no_pdf) is InboundStatus.NEEDS_ACTION
    assert "няма PDF" in (_unknown_card(db_session, no_pdf).subtitle or "")
    assert db_session.scalars(select(AgentRun)).all() == []


def test_duplicate_number_is_idempotent(
    db_session: Session, audit_ctx: AuditContext, tmp_path: Path, stub: Callable[[dict[str, Any]], None]
) -> None:
    stub({"mega_invoice_parser": CASES[0]})
    mail = _mail(db_session, tmp_path, "dup")
    assert mega_invoice_intake.handle(db_session, mail) is InboundStatus.PROCESSED
    assert mega_invoice_intake.handle(db_session, mail) is InboundStatus.PROCESSED
    assert len(db_session.scalars(select(MegaInvoice)).all()) == 1
