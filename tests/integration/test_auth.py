"""/auth/login, /auth/refresh, /auth/logout и current_principal (agro-zmv.1)."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, get_db
from app.core import clock
from app.core.audit_context import AuditContext
from app.core.security import hash_refresh_token
from app.main import create_app
from app.models.audit import AuditLog
from app.models.identity import Actor, Device, RefreshToken, User
from app.services.users import UserService

PASSWORD = "tajna-parola-1"
EMAIL = "georgi@agroimport.bg"


@pytest.fixture
def user(db_session: Session, audit_ctx: AuditContext) -> User:
    created = UserService.create(
        db_session, email=EMAIL, full_name="Георги Гюров", short_name="Георги", password=PASSWORD
    )
    db_session.flush()
    return created


@pytest.fixture
def api(db_session: Session, user: User) -> Iterator[TestClient]:
    app = create_app()
    probe = APIRouter()

    @probe.get("/_probe/me")
    def me(principal: CurrentPrincipal) -> dict[str, int]:  # pyright: ignore[reportUnusedFunction]
        return {"id": principal.user.id}

    app.include_router(probe)

    def override_db() -> Iterator[Session]:
        try:
            yield db_session
            db_session.commit()  # savepoint; външната транзакция на теста се връща назад
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client


def login(api: TestClient, password: str = PASSWORD, email: str = EMAIL, installation: str = "inst-1"):
    return api.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
            "installation_id": installation,
            "platform": "ios",
            "device_model": "iPhone 17",
        },
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_success(api: TestClient, db_session: Session, user: User) -> None:
    response = login(api, email="  Georgi@AgroImport.bg ")
    assert response.status_code == 200
    body = response.json()
    assert body["access_expires_in"] == 900
    assert body["refresh_token"].startswith("r_")
    assert body["user"] == {
        "id": user.id,
        "full_name": "Георги Гюров",
        "short_name": "Георги",
        "email": EMAIL,
    }
    assert body["device"]["biometric_enrolled"] is False
    db_session.refresh(user)
    assert user.last_login_at is not None
    assert user.failed_login_count == 0
    stored = db_session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(body["refresh_token"]))
    )
    assert stored is not None
    assert api.get("/_probe/me", headers=bearer(body["access_token"])).json() == {"id": user.id}


def test_login_same_installation_reuses_device(api: TestClient, db_session: Session) -> None:
    first = login(api).json()["device"]["id"]
    second = login(api).json()["device"]["id"]
    assert first == second
    other = login(api, installation="inst-2").json()["device"]["id"]
    assert other != first


def test_wrong_password_and_unknown_email_same_response(
    api: TestClient, db_session: Session, user: User
) -> None:
    wrong = login(api, password="грешна")
    unknown = login(api, email="nobody@agroimport.bg")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"
    db_session.refresh(user)
    assert user.failed_login_count == 1


def test_lock_on_tenth_failure_then_423(api: TestClient, db_session: Session, user: User) -> None:
    for _ in range(9):
        assert login(api, password="грешна").status_code == 401
    tenth = login(api, password="грешна")
    assert tenth.status_code == 401
    assert tenth.json()["error"]["code"] == "invalid_credentials"
    db_session.refresh(user)
    assert user.locked_until is not None

    locked = login(api)  # дори с вярна парола
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "account_locked"


def test_lock_expires_and_success_resets_counter(api: TestClient, db_session: Session, user: User) -> None:
    with freeze_time(clock.now()) as frozen:
        for _ in range(10):
            login(api, password="грешна")
        assert login(api).status_code == 423
        frozen.tick(timedelta(minutes=16))
        assert login(api).status_code == 200
    db_session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


def test_inactive_user_401(api: TestClient, db_session: Session, user: User, audit_ctx: AuditContext) -> None:
    user.is_active = False
    db_session.flush()
    response = login(api)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "user_inactive"


def test_refresh_rotates(api: TestClient, db_session: Session) -> None:
    first = login(api).json()
    response = api.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert api.get("/_probe/me", headers=bearer(second["access_token"])).status_code == 200
    old = db_session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(first["refresh_token"]))
    )
    assert old is not None
    assert old.rotated_at is not None
    # новият токен се ротира нормално
    assert (
        api.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 200
    )


def test_refresh_reuse_detection_revokes_all(api: TestClient, db_session: Session) -> None:
    first = login(api).json()
    second = api.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

    reuse = api.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "token_invalid"
    # законният наследник също е отнет
    after = api.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert after.status_code == 401
    tokens = db_session.scalars(select(RefreshToken)).all()
    assert tokens
    assert all(t.revoked_at is not None for t in tokens)


def test_refresh_unknown_expired_and_revoked_device(api: TestClient, db_session: Session) -> None:
    assert api.post("/api/v1/auth/refresh", json={"refresh_token": "r_nope"}).status_code == 401

    with freeze_time(clock.now()) as frozen:
        old = login(api).json()
        frozen.tick(timedelta(days=31))
        expired = api.post("/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]})
        assert expired.status_code == 401

    fresh = login(api, installation="inst-2").json()
    device = db_session.get(Device, fresh["device"]["id"])
    assert device is not None
    device.revoked_at = clock.now()
    db_session.flush()
    assert api.post("/api/v1/auth/refresh", json={"refresh_token": fresh["refresh_token"]}).status_code == 401


def test_refresh_inactive_user_401(api: TestClient, db_session: Session, user: User) -> None:
    tokens = login(api).json()
    user.is_active = False
    db_session.flush()
    response = api.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401


def test_logout_revokes_device_and_tokens(api: TestClient, db_session: Session) -> None:
    tokens = login(api).json()
    response = api.post("/api/v1/auth/logout", headers=bearer(tokens["access_token"]))
    assert response.status_code == 204
    device = db_session.get(Device, tokens["device"]["id"])
    assert device is not None
    assert device.revoked_at is not None
    assert (
        api.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    )
    # токенът за достъп спира да важи веднага (устройството е отнето)
    after = api.get("/_probe/me", headers=bearer(tokens["access_token"]))
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "token_invalid"


def test_protected_route_without_or_bad_token(api: TestClient) -> None:
    for headers in ({}, bearer("not-a-jwt")):
        response = api.get("/_probe/me", headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "token_invalid"


def test_protected_route_expired_token(api: TestClient) -> None:
    with freeze_time(clock.now()) as frozen:
        token = login(api).json()["access_token"]
        frozen.tick(timedelta(minutes=16))
        response = api.get("/_probe/me", headers=bearer(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


def test_audit_actor_of_device_on_login_is_user(api: TestClient, db_session: Session, user: User) -> None:
    device_id = login(api).json()["device"]["id"]
    user_actor = db_session.scalar(select(Actor.id).where(Actor.code == f"user:{user.id}"))
    row = db_session.scalar(
        select(AuditLog).where(AuditLog.entity_type == "devices", AuditLog.entity_id == device_id)
    )
    assert row is not None
    assert row.actor_id == user_actor
    assert row.source == "api"


def test_failed_login_audited_as_system_api(api: TestClient, db_session: Session, user: User) -> None:
    login(api, password="грешна")
    system_api = db_session.scalar(select(Actor.id).where(Actor.code == "system:api"))
    row = db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_type == "users", AuditLog.entity_id == user.id, AuditLog.action == "update"
        )
    )
    assert row is not None
    assert row.actor_id == system_api
