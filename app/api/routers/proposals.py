from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, DbSession
from app.models.proposals import FeedbackVerdict
from app.schemas.proposals import ProposalDetailDTO, ProposalFeedbackRequest
from app.services.proposals import get_proposal, proposal_detail, submit_feedback

router = APIRouter(tags=["proposals"])


@router.get("/proposals/{proposal_id}", response_model=ProposalDetailDTO)
def proposal_get(proposal_id: int, _: CurrentPrincipal, session: DbSession) -> ProposalDetailDTO:
    return proposal_detail(session, get_proposal(session, proposal_id))


@router.post("/proposals/{proposal_id}/feedback", response_model=ProposalDetailDTO)
def proposal_feedback(
    proposal_id: int, body: ProposalFeedbackRequest, principal: CurrentPrincipal, session: DbSession
) -> ProposalDetailDTO:
    proposal = submit_feedback(
        session,
        proposal_id,
        verdict=FeedbackVerdict(body.verdict),
        note_text=body.note_text,
        author_id=principal.user.id,
    )
    return proposal_detail(session, proposal)
