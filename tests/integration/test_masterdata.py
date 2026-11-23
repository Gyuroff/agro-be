"""counterparties/domains/contacts/products/warehouses (agro-l2v.4)."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.models.masterdata import Contact, Counterparty, CounterpartyDomain, Product, Warehouse


def _cp(eik: str = "123456789", **kw: object) -> Counterparty:
    return Counterparty(name="Агро Трейд", legal_name="Агро Трейд ООД", eik=eik, **kw)


def test_counterparty_defaults(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp()
    db_session.add(cp)
    db_session.flush()
    db_session.refresh(cp)
    assert (cp.invoicing_mode, cp.country_code, cp.is_active, cp.is_vat_registered) == (
        "shadow",
        "BG",
        True,
        True,
    )


@pytest.mark.parametrize("eik", ["12345678", "1234567890", "12345678a", "1234567890123"[:12], ""])
def test_invalid_eik(db_session: Session, audit_ctx: AuditContext, eik: str) -> None:
    db_session.add(_cp(eik=eik))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("eik", ["123456789", "1234567890123"])
def test_valid_eik(db_session: Session, audit_ctx: AuditContext, eik: str) -> None:
    db_session.add(_cp(eik=eik))
    db_session.flush()


def test_erp_requires_mega_code(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add(_cp(invoicing_mode="erp"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_erp_with_mega_code_ok(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add(_cp(invoicing_mode="erp", mega_code="K1"))
    db_session.flush()


def test_active_email_unique_inactive_duplicate_allowed(db_session: Session, audit_ctx: AuditContext) -> None:
    a, b = _cp("111111111"), _cp("222222222")
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(Contact(counterparty_id=a.id, email="Maria@Agro.bg"))
    db_session.flush()
    # неактивен дубликат е позволен (имейлът е нормализиран от валидатора)
    db_session.add(Contact(counterparty_id=b.id, email="maria@agro.bg", is_active=False))
    db_session.flush()
    # втори активен — не
    db_session.add(Contact(counterparty_id=b.id, email=" MARIA@agro.bg "))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_contact_needs_email_or_phone(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp()
    db_session.add(cp)
    db_session.flush()
    db_session.add(Contact(counterparty_id=cp.id, full_name="Без контакт"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_contact_phone_only_ok(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp()
    db_session.add(cp)
    db_session.flush()
    db_session.add(Contact(counterparty_id=cp.id, phone="+359888000000"))
    db_session.flush()


def test_domain_unique(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp()
    db_session.add(cp)
    db_session.flush()
    db_session.add(CounterpartyDomain(counterparty_id=cp.id, domain="agrotrade.bg"))
    db_session.flush()
    db_session.add(CounterpartyDomain(counterparty_id=cp.id, domain="AGROTRADE.bg"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_product_and_warehouse(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp()
    db_session.add_all([cp, Product(name="Пелети")])
    db_session.flush()
    db_session.add(Warehouse(counterparty_id=cp.id, name="Тервел"))
    db_session.flush()
    db_session.add(Warehouse(counterparty_id=cp.id, name="Тервел"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_product_unit_only_tonnes(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add(Product(name="Тор", unit="kg"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_trigram_index_exists(db_session: Session) -> None:
    from sqlalchemy import text

    definition = db_session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_counterparties_name_trgm'")
    )
    assert definition is not None
    assert "gin_trgm_ops" in definition
