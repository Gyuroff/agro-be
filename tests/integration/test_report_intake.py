"""Агенти „справки“ (agro-6p5.5): писмо `report` → справка + редове + карта (stub LLM)."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.models.cards import ActionCard, AgentRun, CardKind
from app.models.mail import InboundEmail, InboundStatus
from app.models.reports import MatchStatus, SupplierReport, SupplierReportLine
from app.schemas.reports import ReportDTO
from app.services import report_intake
from app.services.report_views import report_dto
from tests.integration.test_report_matching import World

D = Decimal
DAY = date(2026, 11, 11)
GOOD = ["СА 1000 ВХ", "СА 1001 ВХ", "СА 1002 ВХ", "СА 1003 ВХ", "СА 1004 ВХ", "СА 1005 ВХ"] + [
    f"СА 20{i:02d} ВХ" for i in range(5)
]  # 11 редa, всички планирани


class FakeModel:
    def __init__(self, agent: str, outputs: dict[str, dict[str, Any]]) -> None:
        self.agent, self.outputs = agent, outputs

    def with_structured_output(self, schema: type[BaseModel], **_: Any) -> Runnable[Any, Any]:
        def invoke(messages: Any) -> BaseModel:
            data = self.outputs[self.agent]
            if callable(data):
                data = data(messages)
            return schema.model_validate(data)

        return RunnableLambda(invoke)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    def set_outputs(outputs: dict[str, Any]) -> None:
        monkeypatch.setattr("app.agents.llm.get_chat_model", lambda agent: FakeModel(agent, outputs))

    return set_outputs


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext) -> World:
    return World(db_session)


def _mail(db: Session, w: World) -> InboundEmail:
    mail = InboundEmail(
        message_id="<report-intake@x>",
        from_address="sales@agrosuet.bg",
        subject="Справка 11.11",
        body_text="Изпълнени курсове.",
        received_at=clock.now(),
        raw_path="/tmp/x.eml",
        auth_ok=True,
        counterparty_id=w.supplier.id,
        kind="report",
    )
    db.add(mail)
    db.flush()
    return mail


def _row(plate: str, qty: str | None = "24.860", **kw: Any) -> dict[str, Any]:
    return {"load_date": DAY.isoformat(), "plate_raw": plate, "qty_t": qty, **kw}


def test_figma_report_11_1_1_with_substitute(
    db_session: Session, w: World, stub: Callable[[dict[str, Any]], None]
) -> None:
    for plate in GOOD:
        w.trip(plate, DAY)
    old = w.trip("СВ 7890 ТР", DAY)
    stub(
        {
            "report_parser": {"lines": [_row(p) for p in GOOD] + [_row("РВ 2211 ТТ", "25.040")]},
            "report_matcher": lambda _m: {
                "explanations": [{"unplanned_line_id": _unplanned_id(db_session), "explanation": "Сменен."}]
            },
        }
    )
    mail = _mail(db_session, w)

    assert report_intake.handle(db_session, mail) is InboundStatus.NEEDS_ACTION

    report = db_session.scalars(
        select(SupplierReport).where(SupplierReport.inbound_email_id == mail.id)
    ).one()
    assert report.status == "pending" and report.total_rows == 12
    assert report.total_qty_t == D("24.860") * 11 + D("25.040")
    lines = list(
        db_session.scalars(select(SupplierReportLine).where(SupplierReportLine.report_id == report.id))
    )
    by = {s: [ln for ln in lines if ln.match_status == s.value] for s in MatchStatus}
    assert (len(by[MatchStatus.MATCHED]), len(by[MatchStatus.UNPLANNED]), len(by[MatchStatus.MISSING])) == (
        11,
        1,
        1,
    )
    unplanned, missing = by[MatchStatus.UNPLANNED][0], by[MatchStatus.MISSING][0]
    assert unplanned.plate_norm == "PB2211TT" and missing.trip_id == old.id
    assert unplanned.substitute_for_line_id == missing.id and unplanned.warehouse_id == w.wh.id

    card = db_session.scalars(select(ActionCard)).one()
    assert card.kind == CardKind.SUPPLIER_REPORT.value and card.supplier_report_id == report.id
    p = card.payload
    assert (p["matched"], p["unplanned"], p["missing"]) == (11, 1, 1) and len(p["preview"]) == 3
    assert (
        p["substitutes"][0]["new_plate"] == "РВ 2211 ТТ" and p["substitutes"][0]["old_plate"] == "СВ 7890 ТР"
    )
    assert p["customer_names"] == ["Агро Трейд"]
    runs = {r.agent for r in db_session.scalars(select(AgentRun))}
    assert runs == {"report_parser", "report_matcher"}
    matcher = db_session.scalars(select(AgentRun).where(AgentRun.agent == "report_matcher")).one()
    assert matcher.output is not None and matcher.status == "ok"
    assert matcher.output["explanations"][0]["explanation"] == "Сменен."

    # повторно пускане не дублира справката
    assert report_intake.handle(db_session, mail) is InboundStatus.NEEDS_ACTION
    assert (
        len(
            list(db_session.scalars(select(SupplierReport).where(SupplierReport.inbound_email_id == mail.id)))
        )
        == 1
    )


def _unplanned_id(db: Session) -> int:
    return db.scalars(
        select(SupplierReportLine.id).where(SupplierReportLine.match_status == MatchStatus.UNPLANNED.value)
    ).one()


def test_line_without_qty_is_uncertain_and_not_counted(
    db_session: Session, w: World, stub: Callable[[dict[str, Any]], None]
) -> None:
    w.trip("СА 1000 ВХ", DAY)
    w.trip("СА 1001 ВХ", DAY)
    stub({"report_parser": {"lines": [_row("СА 1000 ВХ"), _row("СА 1001 ВХ", None)]}})
    mail = _mail(db_session, w)

    report_intake.handle(db_session, mail)

    no_qty = db_session.scalars(
        select(SupplierReportLine).where(SupplierReportLine.plate_norm == "CA1001BX")
    ).one()
    assert no_qty.qty_t is None and "qty" in no_qty.uncertain_fields
    assert no_qty.match_status == MatchStatus.MATCHED.value  # намерен е, но не се брои
    card = db_session.scalars(select(ActionCard)).one()
    assert card.payload["matched"] == 1 and card.payload["total_rows"] == 2
    report = db_session.get(SupplierReport, card.supplier_report_id)
    assert report is not None
    dto: ReportDTO = report_dto(db_session, report)
    assert dto.counts.matched == 1 and dto.confirm_label_count == 1
    assert [m.plate for m in dto.matched] == ["СА 1000 ВХ"]


def test_kg_converted_to_tonnes(
    db_session: Session, w: World, stub: Callable[[dict[str, Any]], None]
) -> None:
    stub({"report_parser": {"lines": [_row("СА 1000 ВХ", "24860")]}})
    report_intake.handle(db_session, _mail(db_session, w))
    line = db_session.scalars(
        select(SupplierReportLine).where(SupplierReportLine.plate_norm == "CA1000BX")
    ).one()
    assert line.qty_t == D("24.860") and "qty" in line.uncertain_fields
