# ruff: noqa: F811
"""Предложения: GET /proposals/{id} и вердикт Вярно/Грешно (agro-msf.4)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.proposals import ActionProposal, ProposalFeedback, ProposalStatus
from tests.integration.test_mail_send import _env, company  # noqa: F401  (фикстури: GreenMail, фирма)
from tests.integration.test_requests import Setup, api, auth, s  # noqa: F401


@pytest.fixture
def proposal(db_session: Session, s: Setup) -> ActionProposal:
    row = ActionProposal(
        kind="send_request", title="Бих изпратила 2 писма", payload={"emails": [{"to": "a@b.bg"}]}
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_get_proposal_without_feedback(
    api: TestClient, auth: dict[str, str], proposal: ActionProposal
) -> None:
    response = api.get(f"/api/v1/proposals/{proposal.id}", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["kind"], body["status"]) == (proposal.id, "send_request", "pending")
    assert body["payload"] == {"emails": [{"to": "a@b.bg"}]} and body["feedback"] is None


def test_get_unknown_proposal_is_404(api: TestClient, auth: dict[str, str], s: Setup) -> None:
    response = api.get("/api/v1/proposals/999999", headers=auth)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_proposals_require_auth(api: TestClient, proposal: ActionProposal) -> None:
    assert api.get(f"/api/v1/proposals/{proposal.id}").status_code == 401
    assert (
        api.post(f"/api/v1/proposals/{proposal.id}/feedback", json={"verdict": "correct"}).status_code == 401
    )


def test_correct_verdict_confirms_proposal(
    api: TestClient, auth: dict[str, str], proposal: ActionProposal, db_session: Session
) -> None:
    response = api.post(
        f"/api/v1/proposals/{proposal.id}/feedback", json={"verdict": "correct"}, headers=auth
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == ProposalStatus.CONFIRMED_CORRECT.value
    assert body["feedback"] == {"verdict": "correct", "note_text": None}

    row = db_session.scalars(select(ProposalFeedback)).one()
    assert row.proposal_id == proposal.id and row.author_id is not None
    # GET връща същия вердикт
    again = api.get(f"/api/v1/proposals/{proposal.id}", headers=auth).json()
    assert again["feedback"] == {"verdict": "correct", "note_text": None}


def test_wrong_verdict_keeps_note_and_trims_it(
    api: TestClient, auth: dict[str, str], proposal: ActionProposal
) -> None:
    response = api.post(
        f"/api/v1/proposals/{proposal.id}/feedback",
        json={"verdict": "wrong", "note_text": "  Не бива да се праща до Олива.  "},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == ProposalStatus.CONFIRMED_WRONG.value
    assert body["feedback"] == {"verdict": "wrong", "note_text": "Не бива да се праща до Олива."}


def test_wrong_verdict_without_note_is_ok_and_blank_note_becomes_null(
    api: TestClient, auth: dict[str, str], proposal: ActionProposal
) -> None:
    response = api.post(
        f"/api/v1/proposals/{proposal.id}/feedback",
        json={"verdict": "wrong", "note_text": "   "},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    assert response.json()["feedback"] == {"verdict": "wrong", "note_text": None}


def test_note_is_rejected_for_correct(
    api: TestClient, auth: dict[str, str], proposal: ActionProposal, db_session: Session
) -> None:
    response = api.post(
        f"/api/v1/proposals/{proposal.id}/feedback",
        json={"verdict": "correct", "note_text": "всичко е точно"},
        headers=auth,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    db_session.refresh(proposal)
    assert proposal.status == ProposalStatus.PENDING.value
    assert db_session.scalars(select(ProposalFeedback)).all() == []


def test_second_answer_is_409(api: TestClient, auth: dict[str, str], proposal: ActionProposal) -> None:
    first = api.post(f"/api/v1/proposals/{proposal.id}/feedback", json={"verdict": "correct"}, headers=auth)
    assert first.status_code == 200
    second = api.post(f"/api/v1/proposals/{proposal.id}/feedback", json={"verdict": "wrong"}, headers=auth)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_transition"
    assert (
        api.get(f"/api/v1/proposals/{proposal.id}", headers=auth).json()["feedback"]["verdict"] == "correct"
    )


def test_invalid_verdict_is_validation_error(
    api: TestClient, auth: dict[str, str], proposal: ActionProposal
) -> None:
    response = api.post(f"/api/v1/proposals/{proposal.id}/feedback", json={"verdict": "maybe"}, headers=auth)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_feedback_does_not_touch_anything_else(
    api: TestClient, auth: dict[str, str], proposal: ActionProposal, db_session: Session
) -> None:
    before = dict(proposal.payload)
    api.post(f"/api/v1/proposals/{proposal.id}/feedback", json={"verdict": "wrong"}, headers=auth)
    db_session.refresh(proposal)
    assert proposal.payload == before and proposal.kind == "send_request"
