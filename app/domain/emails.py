"""Нормализация на имейли и домейни (docs/data-model.md §0, §5)."""

PUBLIC_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "abv.bg",
        "mail.bg",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "icloud.com",
        "dir.bg",
    }
)


def normalize_email(email: str) -> str:
    """lowercase, без интервали."""
    return "".join(email.split()).lower()


def domain_of(email: str) -> str:
    """Домейнът след последното `@` на нормализирания адрес; `ValueError`, ако няма `@`."""
    normalized = normalize_email(email)
    local, sep, domain = normalized.rpartition("@")
    if not sep or not local or not domain:
        raise ValueError(f"невалиден имейл: {email!r}")
    return domain


def normalize_domain(domain: str) -> str:
    """lowercase, без интервали и без водещо `@`."""
    return "".join(domain.split()).lower().removeprefix("@")


def is_public_domain(domain: str) -> bool:
    return normalize_domain(domain) in PUBLIC_EMAIL_DOMAINS
