"""Админ изгледи: потребители, устройства, фирма, номерация, контрагенти, мастър данни (agro-wo5.2)."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.admin.views import ALL_VIEWS
from app.core.audit_context import AuditContext, AuditSource, audit_context
from app.db.session import get_sessionmaker
from app.main import create_app
from app.models.audit import AuditLog
from app.models.company import NumberSequence
from app.models.identity import Actor, Device, RefreshToken, User
from app.models.masterdata import Counterparty
from app.services.users import UserService

PASSWORD = "admin-parola-1"


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _digits() -> str:
    return str(uuid.uuid4().int)[:9]


def _commit_user(engine: Engine, *, is_admin: bool = False, password: str = PASSWORD) -> User:
    with Session(engine, expire_on_commit=False) as s:
        actor_id = s.scalar(select(Actor.id).where(Actor.code == "system:seed"))
        assert actor_id is not None
        with audit_context(AuditContext(actor_id, AuditSource.SEED)):
            user = UserService.create(
                s,
                email=f"{_uid()}@views.test",
                full_name="Тест",
                short_name="Тест",
                password=password,
                is_admin=is_admin,
            )
            s.commit()
        return user


@pytest.fixture
def web(db_engine: Engine) -> Iterator[TestClient]:
    app = create_app(ALL_VIEWS)
    with TestClient(app, follow_redirects=False) as c:
        admin = _commit_user(db_engine, is_admin=True)
        r = c.post("/admin/login", data={"username": admin.email, "password": PASSWORD})
        assert r.status_code in (302, 303)
        yield c
    # устройствата/токените са комитнати за реално — чистим, за да не пречат на другите тестове
    with db_engine.begin() as conn:
        users = "(SELECT id FROM users WHERE email LIKE '%@views.test')"
        devices = f"(SELECT id FROM devices WHERE user_id IN {users})"
        conn.execute(text(f"DELETE FROM refresh_tokens WHERE device_id IN {devices}"))
        conn.execute(text(f"DELETE FROM devices WHERE user_id IN {users}"))


def test_every_view_lists_in_bulgarian(web: TestClient) -> None:
    for view in ALL_VIEWS:
        identity = view.identity  # pyright: ignore[reportAttributeAccessIssue]
        response = web.get(f"/admin/{identity}/list")
        assert response.status_code == 200, (identity, response.text[:300])
        assert view.name_plural in response.text
        if view.can_create:
            assert web.get(f"/admin/{identity}/create").status_code == 200, identity


def test_user_created_in_admin_can_log_in_to_api(db_engine: Engine, web: TestClient) -> None:
    form = web.get("/admin/user/create")
    assert "password_hash" not in form.text
    assert 'name="new_password"' in form.text

    email = f"{_uid()}@views.test"
    data = {
        "email": email,
        "full_name": "Нов Потребител",
        "short_name": "Нов",
        "is_active": "y",
        "push_enabled": "y",
        "new_password": "nova-parola-77",
    }
    response = web.post("/admin/user/create", data=data)
    assert response.status_code in (302, 303), response.text

    api = web.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "nova-parola-77",
            "installation_id": "adm-1",
            "platform": "ios",
            "device_model": "iPhone",
        },
    )
    assert api.status_code == 200, api.text
    with Session(db_engine) as s:
        user = s.scalar(select(User).where(User.email == email))
        assert user is not None
        assert s.scalar(select(Actor.id).where(Actor.code == f"user:{user.id}")) is not None
        assert web.get(f"/admin/user/details/{user.id}").status_code == 200
        assert "password_hash" not in web.get(f"/admin/user/details/{user.id}").text
        assert web.get(f"/admin/user/edit/{user.id}").status_code == 200
        assert user.password_hash not in web.get(f"/admin/user/edit/{user.id}").text


def test_user_requires_password_on_create(db_engine: Engine, web: TestClient) -> None:
    email = f"{_uid()}@views.test"
    response = web.post(
        "/admin/user/create",
        data={"email": email, "full_name": "Без парола", "short_name": "БП", "is_active": "y"},
    )
    assert response.status_code == 400
    with Session(db_engine) as s:
        assert s.scalar(select(User).where(User.email == email)) is None


def test_edit_user_without_password_keeps_it(db_engine: Engine, web: TestClient) -> None:
    user = _commit_user(db_engine)
    response = web.post(
        f"/admin/user/edit/{user.id}",
        data={"email": user.email, "full_name": "Преименуван", "short_name": "П", "is_active": "y"},
    )
    assert response.status_code in (302, 303), response.text
    with Session(db_engine) as s:
        fresh = s.get(User, user.id)
        assert fresh is not None
        assert fresh.full_name == "Преименуван"
        assert fresh.password_hash == user.password_hash


def test_revoke_device_blocks_refresh(db_engine: Engine, web: TestClient) -> None:
    user = _commit_user(db_engine)
    login = web.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": PASSWORD,
            "installation_id": "rev-1",
            "platform": "android",
            "device_model": "Pixel",
        },
    )
    assert login.status_code == 200, login.text
    refresh = login.json()["refresh_token"]
    with Session(db_engine) as s:
        device = s.scalar(select(Device).where(Device.user_id == user.id))
        assert device is not None
        device_id = device.id

    response = web.get(f"/admin/device/action/revoke?pks={device_id}")
    assert response.status_code in (302, 303), response.text

    with Session(db_engine) as s:
        assert s.get(Device, device_id).revoked_at is not None  # pyright: ignore[reportOptionalMemberAccess]
        tokens = s.scalars(select(RefreshToken).where(RefreshToken.device_id == device_id)).all()
        assert tokens
        assert all(t.revoked_at is not None for t in tokens)
        audit = s.scalar(
            select(AuditLog)
            .where(AuditLog.entity_type == "devices", AuditLog.entity_id == device_id)
            .order_by(AuditLog.id.desc())
        )
        assert audit is not None
        assert audit.source == "admin"
    assert web.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 401


def test_device_and_company_have_no_create(web: TestClient) -> None:
    assert web.get("/admin/device/create").status_code in (302, 303, 400, 403, 404)
    assert web.get("/admin/company-settings/create").status_code in (302, 303, 400, 403, 404)


def test_erp_counterparty_needs_mega_code_and_email(db_engine: Engine, web: TestClient) -> None:
    eik = _digits()
    base = {
        "name": f"Фирма {eik}",
        "legal_name": f"Фирма {eik} ООД",
        "eik": eik,
        "country_code": "BG",
        "invoicing_mode": "erp",
        "order_format": "per_truck",
        "client_file_adapter": "",
        "is_active": "y",
    }
    response = web.post("/admin/counterparty/create", data=base)
    assert response.status_code == 400
    with Session(db_engine) as s:
        assert s.scalar(select(Counterparty).where(Counterparty.eik == eik)) is None

    no_email = web.post("/admin/counterparty/create", data={**base, "mega_code": f"M{eik}"})
    assert no_email.status_code == 400

    shadow = web.post("/admin/counterparty/create", data={**base, "invoicing_mode": "shadow"})
    assert shadow.status_code in (302, 303), shadow.text
    with Session(db_engine) as s:
        row = s.scalar(select(Counterparty).where(Counterparty.eik == eik))
        assert row is not None
        assert row.client_file_adapter is None


def test_counterparty_rejects_bad_eik_and_unknown_adapter(web: TestClient) -> None:
    base = {
        "name": "Х",
        "legal_name": "Х ООД",
        "country_code": "BG",
        "invoicing_mode": "shadow",
        "order_format": "per_truck",
        "is_active": "y",
    }
    assert web.post("/admin/counterparty/create", data={**base, "eik": "12ab"}).status_code == 400
    bad = web.post(
        "/admin/counterparty/create", data={**base, "eik": _digits(), "client_file_adapter": "nqma"}
    )
    assert bad.status_code == 400


def test_number_sequence_cannot_drop_below_last_issued(
    db_engine: Engine, web: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = f"t_{_uid()}"
    with Session(db_engine) as s:
        actor_id = s.scalar(select(Actor.id).where(Actor.code == "system:seed"))
        assert actor_id is not None
        with audit_context(AuditContext(actor_id, AuditSource.SEED)):
            seq = NumberSequence(code=code, description="тест", next_number=10)
            s.add(seq)
            s.commit()
        seq_id = seq.id
    data = {"code": code, "description": "тест", "width": "10", "is_active": "y"}
    url = f"/admin/number-sequence/edit/{seq_id}"
    assert web.post(url, data={**data, "next_number": "5"}).status_code in (302, 303)  # няма издадени

    monkeypatch.setattr("app.admin.views.identity._last_issued", lambda _sequence_id: 100)  # издадена №100
    assert web.post(url, data={**data, "next_number": "100"}).status_code == 400
    assert web.post(url, data={**data, "next_number": "50"}).status_code == 400
    assert web.post(url, data={**data, "next_number": "101"}).status_code in (302, 303)
    with Session(db_engine) as s:
        assert s.get(NumberSequence, seq_id).next_number == 101  # pyright: ignore[reportOptionalMemberAccess]


# --- agro-wo5.3: договори, цени, разходи ------------------------------------------------------------


def _seed_commit(engine: Engine, *objects: object) -> None:
    with Session(engine, expire_on_commit=False) as s:
        actor_id = s.scalar(select(Actor.id).where(Actor.code == "system:seed"))
        assert actor_id is not None
        with audit_context(AuditContext(actor_id, AuditSource.SEED)):
            s.add_all(objects)
            s.commit()


def _cleanup(engine: Engine, sql: list[str]) -> None:
    with engine.begin() as conn:
        for stmt in sql:
            conn.execute(text(stmt))


def test_sale_contract_with_other_product_supply_is_rejected(db_engine: Engine, web: TestClient) -> None:
    from datetime import date

    from app.models.contracts import Contract
    from app.models.masterdata import Product, Warehouse

    uid = _uid()
    client = Counterparty(name=f"К {uid}", legal_name=f"К {uid}", eik=_digits(), country_code="BG")
    supplier = Counterparty(name=f"Д {uid}", legal_name=f"Д {uid}", eik=_digits(), country_code="BG")
    p1, p2 = Product(name=f"П1 {uid}", unit="t"), Product(name=f"П2 {uid}", unit="t")
    _seed_commit(db_engine, client, supplier, p1, p2)
    wh = Warehouse(counterparty_id=supplier.id, name=f"С {uid}")
    _seed_commit(db_engine, wh)
    purchase = Contract(
        number=f"П-{uid}",
        side="purchase",
        counterparty_id=supplier.id,
        product_id=p1.id,
        warehouse_id=wh.id,
        valid_from=date(2026, 1, 1),
    )
    _seed_commit(db_engine, purchase)
    try:
        base = {
            "number": f"Р-{uid}",
            "side": "sale",
            "counterparty": str(client.id),
            "valid_from": "2026-01-01",
            "status": "active",
            "opening_drawn_qty_t": "0",
            "planned_truck_qty_t": "25.200",
            "supply_contract": str(purchase.id),
        }
        bad = web.post("/admin/contract/create", data={**base, "product": str(p2.id)})
        assert bad.status_code == 400
        assert "същата стока" in bad.text
        ok = web.post("/admin/contract/create", data={**base, "product": str(p1.id)})
        assert ok.status_code in (302, 303), ok.text
    finally:
        _cleanup(
            db_engine,
            [
                f"DELETE FROM contracts WHERE number = 'Р-{uid}'",
                f"DELETE FROM contracts WHERE number = 'П-{uid}'",
                f"DELETE FROM warehouses WHERE name = 'С {uid}'",
                f"DELETE FROM products WHERE name IN ('П1 {uid}', 'П2 {uid}')",
                f"DELETE FROM counterparties WHERE name IN ('К {uid}', 'Д {uid}')",
            ],
        )


def test_contract_price_cannot_be_edited(web: TestClient) -> None:
    view = next(v for v in ALL_VIEWS if v.identity == "contract-price")  # pyright: ignore[reportAttributeAccessIssue]
    assert view.can_edit is False
    assert view.can_create is True
    assert web.get("/admin/contract-price/edit/1").status_code in (302, 303, 400, 403, 404)


def test_warehouse_packagings_multiselect_in_admin(db_engine: Engine, web: TestClient) -> None:
    from app.models.masterdata import Warehouse

    uid = _uid()
    supplier = Counterparty(name=f"Д {uid}", legal_name=f"Д {uid}", eik=_digits(), country_code="BG")
    _seed_commit(db_engine, supplier)
    base = {"counterparty": str(supplier.id), "name": f"С {uid}"}
    try:
        none = web.post("/admin/warehouse/create", data=base)
        assert none.status_code == 400  # празен избор не е позволен
        bad = web.post("/admin/warehouse/create", data={**base, "packagings": "pallet"})
        assert bad.status_code == 400
        ok = web.post("/admin/warehouse/create", data={**base, "packagings": "bulk"})
        assert ok.status_code in (302, 303), ok.text
        with get_sessionmaker()() as s:
            wh = s.scalars(select(Warehouse).where(Warehouse.name == f"С {uid}")).one()
            assert wh.packagings == ["bulk"]
    finally:
        _cleanup(
            db_engine,
            [
                f"DELETE FROM warehouses WHERE name = 'С {uid}'",
                f"DELETE FROM counterparties WHERE name = 'Д {uid}'",
            ],
        )


def test_contract_price_form_has_packaging_choice(web: TestClient) -> None:
    page = web.get("/admin/contract-price/create")
    assert page.status_code == 200
    assert 'name="packaging"' in page.text
    assert "Насипни" in page.text and "Опаковани" in page.text


def test_expense_delete_is_audited(db_engine: Engine, web: TestClient) -> None:
    from datetime import date
    from decimal import Decimal

    from app.models.result import Expense, ExpenseCategory

    uid = _uid()
    category = ExpenseCategory(name=f"Кат {uid}")
    _seed_commit(db_engine, category)
    expense = Expense(
        category_id=category.id, kind="monthly", amount=Decimal("100.00"), start_month=date(2026, 11, 1)
    )
    _seed_commit(db_engine, expense)
    try:
        response = web.request("DELETE", f"/admin/expense/delete?pks={expense.id}")
        assert response.status_code == 200, response.text
        with Session(db_engine) as s:
            assert s.get(Expense, expense.id) is None
            audit = s.scalar(
                select(AuditLog).where(
                    AuditLog.entity_type == "expenses",
                    AuditLog.entity_id == expense.id,
                    AuditLog.action == "delete",
                )
            )
            assert audit is not None
            assert audit.source == "admin"
    finally:
        _cleanup(db_engine, [f"DELETE FROM expense_categories WHERE name = 'Кат {uid}'"])
