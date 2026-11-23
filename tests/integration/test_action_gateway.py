# ruff: noqa: F811
"""ActionGateway (agro-msf.2): при `advisory` външният ефект е блокиран и се записва предложение."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.models.cards import ActionCard, CardStatus
from app.models.mail import OutboundEmail
from app.models.proposals import ActionProposal, ProposalStatus
from app.models.trips import Trip
from app.services.cards import CardService
from tests.integration.test_mail_send import _env, company  # noqa: F401  (фикстури: GreenMail, фирма)
from tests.integration.test_payment_question import _card as _payment_card
from tests.integration.test_requests import Setup, api, auth, dispatched, s  # noqa: F401


def _set_mode(db: Session, mode: str) -> None:
    from app.models.company import CompanySettings

    db.get_one(CompanySettings, 1).action_mode = mode
    db.flush()


def test_default_mode_is_advisory(db_session: Session, audit_ctx: AuditContext) -> None:
    from app.models.company import CompanySettings

    assert db_session.get_one(CompanySettings, 1).action_mode == "advisory"


def test_advisory_send_blocks_everything_and_records_the_same_letters_as_preview(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    preview = api.get(f"/api/v1/requests/{s.request.id}/preview", headers=auth).json()
    response = api.post(f"/api/v1/cards/{s.card.id}/actions/send", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["snackbar"] is None and body["undo_token"] is None
    assert body["card"]["id"] == s.card.id  # картата остава отворена
    proposal = body["proposal"]
    assert (proposal["kind"], proposal["status"]) == ("send_request", "pending")
    assert proposal["title"] == "Бих изпратила 1 писмо"
    assert proposal["payload"] == preview  # същият код, същият резултат

    # нищо не е изпълнено
    assert db_session.scalars(select(OutboundEmail)).all() == []
    assert db_session.scalars(select(Trip)).all() == []
    assert dispatched == []
    card = db_session.get_one(ActionCard, s.card.id)
    assert card.status == CardStatus.OPEN.value and card.resolution is None

    row = db_session.scalars(select(ActionProposal)).one()
    assert row.card_id == s.card.id and row.status == ProposalStatus.PENDING.value
    assert row.requested_by_id is not None and row.deal_ref == f"client_requests:{s.request.id}"


def test_live_send_still_executes(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    _set_mode(db_session, "live")
    body = api.post(f"/api/v1/cards/{s.card.id}/actions/send", headers=auth).json()
    assert body["proposal"] is None and body["snackbar"] and body["undo_token"]
    assert db_session.scalars(select(ActionProposal)).all() == []
    assert len(db_session.scalars(select(OutboundEmail)).all()) == 1 and len(dispatched) == 1


def test_advisory_reply_records_proposal_and_sends_nothing(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    card = _payment_card(db_session, monkeypatch, tmp_path)  # type: ignore[arg-type]
    _, result = CardService.perform_action(db_session, card.id, "reply_paid", {"paid_on": "2026-11-16"})
    assert result.snackbar is None and result.undo_token is None and result.proposal is not None
    assert result.proposal.kind == "reply_payment"
    assert result.proposal.payload["to"] == "acc@amar.bg"
    assert result.proposal.payload["subject"].startswith("Re: фактура в падеж")
    assert result.proposal.payload["body_text"].startswith("Платено е на 16.11.2026.")
    assert db_session.scalars(select(OutboundEmail)).all() == []
    assert card.status == CardStatus.OPEN.value


def test_advisory_still_validates_input(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from app.core.errors import DomainError

    card = _payment_card(db_session, monkeypatch, tmp_path)  # type: ignore[arg-type]
    with pytest.raises(DomainError):
        CardService.perform_action(db_session, card.id, "reply_will_pay", {})
    assert db_session.scalars(select(ActionProposal)).all() == []


def test_internal_actions_stay_real_in_advisory(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    card = _payment_card(db_session, monkeypatch, tmp_path)  # type: ignore[arg-type]
    CardService.perform_action(db_session, card.id, "ignore", {})
    assert card.status == CardStatus.DISMISSED.value
    assert db_session.scalars(select(ActionProposal)).all() == []


def test_me_config_exposes_action_mode(api: TestClient, auth: dict[str, str]) -> None:
    assert api.get("/api/v1/me/config", headers=auth).json()["action_mode"] == "advisory"
