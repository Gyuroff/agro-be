"""CardService — единственото място, което създава и затваря `action_cards` (docs/data-model.md §10).

- `create` валидира payload по `PAYLOAD_MODELS[kind]`; при отворена карта със същия `dedupe_key` връща нея.
- След успешен commit на сесията се викат куките `on_card_created` (пуш, T8.1; засега празен регистър).
- `perform_action` е диспечерът на `POST /cards/{id}/actions/{action}`: заключва картата (FOR UPDATE),
  проверява, че е отворена, и вика handler от регистъра `{(kind, action): handler}`.
  Handler-ите за видовете се регистрират от задачите T4.5–T4.8, T7.x; тук е само общото `hide`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import get_audit_context
from app.core.errors import DomainError
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.schemas.cards import PAYLOAD_MODELS

log = logging.getLogger(__name__)

_PENDING_KEY = "pending_card_created"

CardCreatedHook = Callable[[int], None]
_created_hooks: list[CardCreatedHook] = []


def register_on_card_created(hook: CardCreatedHook) -> None:
    """Кука `hook(card_id)`, викана СЛЕД commit на транзакцията, създала новата карта."""
    _created_hooks.append(hook)


def _after_commit(session: Session) -> None:
    pending: list[int] = session.info.pop(_PENDING_KEY, [])
    for card_id in pending:
        for hook in _created_hooks:
            try:
                hook(card_id)
            except Exception:  # куката (пуш) не бива да обръща вече записана карта в грешка
                log.exception("on_card_created hook failed for card %s", card_id)


def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


if not event.contains(Session, "after_commit", _after_commit):
    event.listen(Session, "after_commit", _after_commit)
if not event.contains(Session, "after_rollback", _after_rollback):
    event.listen(Session, "after_rollback", _after_rollback)


class CardNotOpen(DomainError):
    def __init__(self) -> None:
        super().__init__("card_not_open", "Картата вече е обработена.", http_status=409)


@dataclass(frozen=True)
class ActionResult:
    snackbar: str
    undo_token: str | None = None


ActionHandler = Callable[[Session, ActionCard, dict[str, Any]], ActionResult]
_handlers: dict[tuple[CardKind, str], ActionHandler] = {}


def register_action(kind: CardKind, action: str, handler: ActionHandler) -> None:
    key = (kind, action)
    if key in _handlers:
        raise RuntimeError(f"Действието {kind.value}/{action} вече е регистрирано.")
    _handlers[key] = handler


# Видовете с общо „Скрий“ (docs/data-model.md §10, таблицата на бутоните).
HIDE_KINDS = (
    CardKind.INVOICE_MISSING,
    CardKind.CONTRACT_OVER,
    CardKind.MAILBOX_ALARM,
    CardKind.STORAGE_UPLOAD_FAILED,
    CardKind.SUPPLIER_ADVANCE_LOW,
    CardKind.STOCK_LOW,
)


def _hide(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    CardService.resolve(session, card, CardStatus.DISMISSED, CardResolution.HIDDEN)
    return ActionResult("Картата е скрита")


for _kind in HIDE_KINDS:
    register_action(_kind, "hide", _hide)


class CardService:
    @staticmethod
    def create_or_get(
        session: Session,
        kind: CardKind,
        *,
        title: str,
        payload: BaseModel | dict[str, Any],
        subtitle: str | None = None,
        dedupe_key: str | None = None,
        call_phone: str | None = None,
        counterparty_id: int | None = None,
        inbound_email_id: int | None = None,
        outbound_email_id: int | None = None,
        client_request_id: int | None = None,
        supplier_report_id: int | None = None,
        contract_id: int | None = None,
        invoice_id: int | None = None,
        invoice_comparison_id: int | None = None,
        one_shot_deal_id: int | None = None,
    ) -> tuple[ActionCard, bool]:
        """Връща `(карта, нова)`. Отворена карта със същия `dedupe_key` се връща непроменена."""
        if dedupe_key is not None:
            existing = CardService._open_by_key(session, dedupe_key)
            if existing is not None:
                return existing, False

        raw = payload.model_dump() if isinstance(payload, BaseModel) else payload
        validated = PAYLOAD_MODELS[kind].model_validate(raw).model_dump(mode="json")
        card = ActionCard(
            kind=kind.value,
            title=title,
            subtitle=subtitle,
            payload=validated,
            dedupe_key=dedupe_key,
            call_phone=call_phone,
            counterparty_id=counterparty_id,
            inbound_email_id=inbound_email_id,
            outbound_email_id=outbound_email_id,
            client_request_id=client_request_id,
            supplier_report_id=supplier_report_id,
            contract_id=contract_id,
            invoice_id=invoice_id,
            invoice_comparison_id=invoice_comparison_id,
            one_shot_deal_id=one_shot_deal_id,
        )
        try:
            # Savepoint: паралелна транзакция може да е създала същата карта (частичен UNIQUE).
            with session.begin_nested():
                session.add(card)
                session.flush()
        except IntegrityError:
            if dedupe_key is not None:
                existing = CardService._open_by_key(session, dedupe_key)
                if existing is not None:
                    return existing, False
            raise
        session.info.setdefault(_PENDING_KEY, []).append(card.id)
        return card, True

    @staticmethod
    def create(session: Session, kind: CardKind, **kwargs: Any) -> ActionCard:
        return CardService.create_or_get(session, kind, **kwargs)[0]

    @staticmethod
    def _open_by_key(session: Session, dedupe_key: str) -> ActionCard | None:
        return session.scalar(
            select(ActionCard).where(
                ActionCard.dedupe_key == dedupe_key, ActionCard.status == CardStatus.OPEN.value
            )
        )

    @staticmethod
    def resolve(
        session: Session,
        card: ActionCard,
        status: CardStatus,
        resolution: CardResolution,
        *,
        at: datetime | None = None,
    ) -> ActionCard:
        """Затваря отворена карта (`done`/`dismissed`); `resolved_by_id` е актьорът от одитния контекст."""
        if card.status != CardStatus.OPEN.value:
            raise CardNotOpen
        if status not in (CardStatus.DONE, CardStatus.DISMISSED):
            raise ValueError("resolve приема само done/dismissed; superseded е за системата")
        card.status = status.value
        card.resolution = resolution.value
        card.resolved_at = at or clock.now()
        card.resolved_by_id = get_audit_context().actor_id
        session.flush()
        return card

    @staticmethod
    def perform_action(
        session: Session, card_id: int, action: str, body: dict[str, Any]
    ) -> tuple[ActionCard, ActionResult]:
        card = session.scalar(select(ActionCard).where(ActionCard.id == card_id).with_for_update())
        if card is None:
            raise DomainError("not_found", "Картата не е намерена.", http_status=404)
        handler = _handlers.get((CardKind(card.kind), action))
        if handler is None:
            raise DomainError("not_found", "Непознато действие.", http_status=404)
        if card.status != CardStatus.OPEN.value:
            raise CardNotOpen
        result = handler(session, card, body)
        session.flush()
        return card, result
