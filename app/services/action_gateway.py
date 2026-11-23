"""ActionGateway — единственият шлюз за действия с външен ефект (docs/advisory-mode.md).

`live` изпълнява истинския handler. `advisory` НЕ го вика: строи предложението със същия код (`build`,
напр. `RequestService.preview`, което ползва `compose`) и го записва в `action_proposals`.
Вътрешните записи не минават оттук — те са истински и в двата режима.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_context import get_audit_context
from app.models.cards import ActionCard
from app.models.company import CompanySettings
from app.models.proposals import ActionMode, ActionProposal


@dataclass(frozen=True)
class ProposalSpec:
    kind: str
    title: str
    payload: dict[str, Any]
    deal_ref: str | None = None


# `build(session, card, body)` — без странични ефекти върху външния свят; невалиден вход вдига същите грешки.
ProposalBuilder = Callable[[Session, ActionCard, dict[str, Any]], ProposalSpec]


def current_mode(session: Session) -> ActionMode:
    value = session.scalar(select(CompanySettings.action_mode).where(CompanySettings.id == 1))
    return ActionMode(value) if value else ActionMode.ADVISORY  # без настройка: безопасният режим


def is_advisory(session: Session) -> bool:
    return current_mode(session) is ActionMode.ADVISORY


def propose(session: Session, card: ActionCard | None, spec: ProposalSpec) -> ActionProposal:
    proposal = ActionProposal(
        kind=spec.kind,
        card_id=card.id if card is not None else None,
        deal_ref=spec.deal_ref,
        title=spec.title,
        payload=spec.payload,
        requested_by_id=get_audit_context().actor_id,
    )
    session.add(proposal)
    session.flush()
    return proposal
