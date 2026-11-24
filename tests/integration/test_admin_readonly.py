"""Админ: изгледи само за четене (agro-wo5.4)."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.admin.base import ReadOnlyModelView
from app.admin.views import ALL_VIEWS
from app.core import clock
from app.core.audit_context import AuditContext
from app.models.audit import AuditLog
from app.models.mail import InboundEmail, InboundStatus
from app.services.inbound_mail import reset_for_reprocess
from tests.integration.test_admin_views import web  # noqa: F401  (фикстура)

READONLY = [v for v in ALL_VIEWS if issubclass(v, ReadOnlyModelView)]


def test_readonly_views_have_no_write_buttons(web: TestClient) -> None:  # noqa: F811
    assert len(READONLY) == 9
    for view in READONLY:
        assert not (view.can_create or view.can_edit or view.can_delete), view.name
        identity = view.identity  # pyright: ignore[reportAttributeAccessIssue]
        assert web.get(f"/admin/{identity}/list").status_code == 200
        assert web.get(f"/admin/{identity}/create").status_code in (403, 404, 405), identity


def test_audit_filters_by_entity_actor_source(web: TestClient) -> None:  # noqa: F811
    page = web.get("/admin/audit-log/list")
    assert page.status_code == 200
    for param in ("source", "action", "entity_type", "actor_id"):
        assert param in page.text
    assert web.get("/admin/audit-log/list?source=admin&actor_id=1&entity_type=users").status_code == 200


def _email(db: Session, status: InboundStatus) -> InboundEmail:
    mail = InboundEmail(
        message_id=f"<{uuid.uuid4().hex}@x>",
        from_address="a@b.bg",
        received_at=clock.now(),
        raw_path="/tmp/x.eml",
        auth_ok=True,
        status=status.value,
        error="boom" if status is InboundStatus.FAILED else None,
        processed_at=clock.now() if status is not InboundStatus.NEW else None,
    )
    db.add(mail)
    db.flush()
    return mail


def test_reset_for_reprocess_only_reprocessable(db_session: Session, audit_ctx: AuditContext) -> None:
    failed = _email(db_session, InboundStatus.FAILED)
    needs = _email(db_session, InboundStatus.NEEDS_ACTION)
    done = _email(db_session, InboundStatus.PROCESSED)

    ready = reset_for_reprocess(db_session, [failed.id, needs.id, done.id])

    assert sorted(ready) == sorted([failed.id, needs.id])
    for mail in (failed, needs):
        db_session.refresh(mail)
        assert (mail.status, mail.error, mail.processed_at) == ("new", None, None)
    db_session.refresh(done)
    assert done.status == "processed"
    assert db_session.query(AuditLog).count() > 0
