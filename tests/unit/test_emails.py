import pytest

from app.domain.emails import PUBLIC_EMAIL_DOMAINS, domain_of, normalize_email
from app.models.masterdata import CounterpartyDomain


def test_normalize_email() -> None:
    assert normalize_email("  Maria@AgroTrade.BG ") == "maria@agrotrade.bg"


def test_domain_of() -> None:
    assert domain_of("Maria@AgroTrade.bg") == "agrotrade.bg"
    with pytest.raises(ValueError):
        domain_of("no-at-sign")


def test_public_domains_listed() -> None:
    assert {"gmail.com", "abv.bg", "dir.bg", "icloud.com"} <= PUBLIC_EMAIL_DOMAINS


@pytest.mark.parametrize("domain", ["gmail.com", "GMAIL.com", "@abv.bg", " yahoo.com "])
def test_public_domain_rejected(domain: str) -> None:
    with pytest.raises(ValueError):
        CounterpartyDomain(counterparty_id=1, domain=domain)


def test_domain_normalized() -> None:
    assert CounterpartyDomain(counterparty_id=1, domain="@AgroTrade.BG").domain == "agrotrade.bg"
