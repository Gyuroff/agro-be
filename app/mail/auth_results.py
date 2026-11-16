"""`Authentication-Results` (RFC 8601) → spf/dkim/dmarc и `auth_ok` (docs/data-model.md §7).

Вярваме само на хедъри с нашия authserv-id (`APP_MAIL_AUTHSERV_ID`): всичко друго може да е
подправено от подателя. Без настроен authserv-id никой хедър не е доверен → `auth_ok=False`.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

_METHODS = ("spf", "dkim", "dmarc")
_HEADER = "authentication-results"


@dataclass(frozen=True)
class AuthResult:
    spf: str | None = None
    dkim: str | None = None
    dmarc: str | None = None

    @property
    def auth_ok(self) -> bool:
        """(`spf=pass` ИЛИ `dkim=pass`) И dmarc не е `fail`."""
        return (self.spf == "pass" or self.dkim == "pass") and self.dmarc != "fail"


def _strip_comments(value: str) -> str:
    return re.sub(r"\([^)]*\)", " ", value)


def _authserv_id(header_value: str) -> str:
    head = _strip_comments(header_value).split(";", 1)[0].split()
    return head[0].lower() if head else ""


def parse_authentication_results(headers: Iterable[tuple[str, str]], authserv_id: str | None) -> AuthResult:
    """Първият (най-горният = най-новият) хедър с нашия authserv-id печели; чужди се пренебрегват."""
    if not authserv_id:
        return AuthResult()
    wanted = authserv_id.strip().lower()
    for name, value in headers:
        if name.lower() != _HEADER or _authserv_id(value) != wanted:
            continue
        found: dict[str, str] = {}
        for clause in _strip_comments(value).split(";")[1:]:
            match = re.match(r"\s*(\w+)\s*=\s*(\w+)", clause)
            if match and match.group(1).lower() in _METHODS:
                found.setdefault(match.group(1).lower(), match.group(2).lower())
        return AuthResult(found.get("spf"), found.get("dkim"), found.get("dmarc"))
    return AuthResult()
