"""Предложения от съветния режим: четене и вердикт Вярно/Грешно (docs/advisory-mode.md, docs/api.md)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models.proposals import (
    ActionProposal,
    FeedbackVerdict,
    ProposalFeedback,
    ProposalStatus,
)
from app.schemas.proposals import ProposalDetailDTO, ProposalFeedbackDTO


def get_proposal(session: Session, proposal_id: int) -> ActionProposal:
    proposal = session.get(ActionProposal, proposal_id)
    if proposal is None:
        raise DomainError("not_found", "Предложението не е намерено.", http_status=404)
    return proposal


def _feedback_of(session: Session, proposal_id: int) -> ProposalFeedback | None:
    return session.scalar(select(ProposalFeedback).where(ProposalFeedback.proposal_id == proposal_id))


def proposal_detail(session: Session, proposal: ActionProposal) -> ProposalDetailDTO:
    feedback = _feedback_of(session, proposal.id)
    return ProposalDetailDTO(
        id=proposal.id,
        kind=proposal.kind,
        title=proposal.title,
        payload=proposal.payload,
        status=proposal.status,
        created_at=proposal.created_at,
        feedback=(
            ProposalFeedbackDTO(verdict=feedback.verdict, note_text=feedback.note_text)  # type: ignore[arg-type]
            if feedback is not None
            else None
        ),
    )


def submit_feedback(
    session: Session,
    proposal_id: int,
    *,
    verdict: FeedbackVerdict,
    note_text: str | None,
    author_id: int,
) -> ActionProposal:
    """Един вердикт на цялото предложение. Не променя нищо друго и не учи агента сам."""
    proposal = get_proposal(session, proposal_id)
    if proposal.status != ProposalStatus.PENDING.value:
        raise DomainError("invalid_transition", "На предложението вече е отговорено.", http_status=409)
    note = (note_text or "").strip() or None
    if note is not None and verdict is not FeedbackVerdict.WRONG:
        raise DomainError(
            "validation_error",
            "Бележка се приема само при „Грешно“.",
            fields={"note_text": "Само при „Грешно“"},
        )
    session.add(
        ProposalFeedback(proposal_id=proposal.id, verdict=verdict.value, note_text=note, author_id=author_id)
    )
    proposal.status = (
        ProposalStatus.CONFIRMED_CORRECT
        if verdict is FeedbackVerdict.CORRECT
        else ProposalStatus.CONFIRMED_WRONG
    ).value
    session.flush()
    return proposal
