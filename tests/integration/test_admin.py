"""SQLAdmin: вход, права, одитен контекст (agro-wo5.1)."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.admin.base import AuditedModelView
from app.core.audit_context import AuditContext, AuditSource, audit_context
from app.main import create_app
from app.models.audit import AuditLog
from app.models.identity import Actor, User
from app.models.masterdata import Product
from app.services.users import UserService

PASSWORD = "admin-parola-1"


class ProductTestView(AuditedModelView, model=Product):
    column_list = ["id", "name"]


def _make_user(engine: Engine, *, is_admin: bool, is_active: bool = True) -> str:
    """Комитва реален потребител (админът ползва собствени връзки, не транзакцията на теста)."""
    email = f"{uuid.uuid4().hex[:10]}@admin.test"
    with Session(engine) as s:
        actor_id = s.scalar(select(Actor.id).where(Actor.code == "system:seed"))
        assert actor_id is not None
        with audit_context(AuditContext(actor_id, AuditSource.SEED)):
            user = UserService.create(
                s, email=email, full_name="Админ", short_name="Адм", password=PASSWORD, is_admin=is_admin
            )
            user.is_active = is_active
            s.commit()
    return email


@pytest.fixture
def admin_client(db_engine: Engine) -> Iterator[TestClient]:
    app = create_app([ProductTestView])
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/admin/login", data={"username": email, "password": password})


def test_anonymous_redirected_to_login(client: TestClient) -> None:
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert response.headers["location"].endswith("/admin/login")


def test_non_admin_cannot_login(db_engine: Engine, client: TestClient) -> None:
    email = _make_user(db_engine, is_admin=False)
    response = _login(client, email)
    assert response.status_code == 400
    assert client.get("/admin/", follow_redirects=False).status_code in (302, 303)


def test_inactive_admin_and_bad_password_rejected(db_engine: Engine, client: TestClient) -> None:
    inactive = _make_user(db_engine, is_admin=True, is_active=False)
    assert _login(client, inactive).status_code == 400
    ok = _make_user(db_engine, is_admin=True)
    assert _login(client, ok, "грешна").status_code == 400
    assert _login(client, "няма@admin.test").status_code == 400


def test_admin_create_is_audited_with_admin_source(db_engine: Engine, admin_client: TestClient) -> None:
    email = _make_user(db_engine, is_admin=True)
    assert _login(admin_client, email).status_code in (302, 303)
    name = f"Стока {uuid.uuid4().hex[:8]}"
    response = admin_client.post(
        "/admin/product/create", data={"name": name, "unit": "t", "vat_rate": "20.00", "is_active": "y"}
    )
    assert response.status_code in (302, 303), response.text

    with Session(db_engine) as s:
        product = s.scalar(select(Product).where(Product.name == name))
        assert product is not None
        row = s.scalar(
            select(AuditLog).where(AuditLog.entity_type == "products", AuditLog.entity_id == product.id)
        )
        assert row is not None
        user = s.scalar(select(User).where(User.email == email))
        assert user is not None
        admin_actor = s.scalar(select(Actor.id).where(Actor.user_id == user.id))
        assert (row.source, row.action, row.actor_id) == ("admin", "create", admin_actor)
        assert row.request_id
        assert admin_client.get(f"/admin/product/details/{product.id}").status_code == 200
        assert admin_client.get("/admin/product/list").status_code == 200
        s.execute(text("DELETE FROM products WHERE id = :i"), {"i": product.id})
        s.commit()


def test_logout_clears_session(db_engine: Engine, admin_client: TestClient) -> None:
    email = _make_user(db_engine, is_admin=True)
    _login(admin_client, email)
    assert admin_client.get("/admin/").status_code == 200
    admin_client.get("/admin/logout")
    assert admin_client.get("/admin/").status_code in (302, 303)
