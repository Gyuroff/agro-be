"""Парсване на суров RFC 822 в `ParsedEmail` (docs/architecture.md §5). Чиста функция, без база и диск."""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import cast

from app.domain.emails import normalize_email

_REFERENCE_RE = re.compile(r"\[\s*(?:АИТ|AIT)\s*-\s*(\d+)\s*\]", re.IGNORECASE)
_MSGID_RE = re.compile(r"<[^<>\s]+>")


@dataclass(frozen=True)
class ParsedAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str
    from_address: str
    from_name: str | None
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str
    date: datetime | None
    in_reply_to: str | None
    references: list[str]
    body_text: str
    body_html: str | None
    attachments: list[ParsedAttachment] = field(default_factory=lambda: [])
    headers: list[tuple[str, str]] = field(default_factory=lambda: [])  # всички хедъри, в реда им


def extract_reference_code(subject: str | None) -> str | None:
    """`[АИТ-142]` / `[AIT-0142]` → `АИТ-0142` (кирилица, 4 цифри) — като `outbound_emails.reference_code`."""
    if not subject:
        return None
    match = _REFERENCE_RE.search(subject)
    return f"АИТ-{int(match.group(1)):04d}" if match else None


def synthetic_message_id(raw: bytes) -> str:
    return f"<{hashlib.sha256(raw).hexdigest()}@local>"


_BLOCK_TAGS = frozenset({"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"})


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HtmlText()
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def _addresses(msg: EmailMessage, header: str) -> list[str]:
    values = [str(v) for v in msg.get_all(header, [])]
    return [normalize_email(addr) for _, addr in getaddresses(values) if addr]


def _first_address(msg: EmailMessage) -> tuple[str, str | None]:
    try:
        header = msg["From"]
        addresses = cast("tuple[Address, ...]", getattr(header, "addresses", ()))
        if addresses:
            first = addresses[0]
            return normalize_email(first.addr_spec), (first.display_name or None)
    except Exception:
        pass
    pairs = getaddresses([str(msg.get("From", ""))])
    if pairs and pairs[0][1]:
        return normalize_email(pairs[0][1]), (pairs[0][0] or None)
    return "", None


def _parse_date(msg: EmailMessage) -> datetime | None:
    try:
        value = msg["Date"]
        parsed = cast("datetime | None", getattr(value, "datetime", None))
    except Exception:
        return None
    if parsed is None:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _part_text(part: EmailMessage | None) -> str | None:
    if part is None:
        return None
    try:
        return str(part.get_content())
    except (LookupError, UnicodeDecodeError):
        payload = cast("bytes", part.get_payload(decode=True) or b"")
        return payload.decode("utf-8", errors="replace")


def _message_ids(value: str | None) -> list[str]:
    return _MSGID_RE.findall(value or "")


def parse(raw: bytes) -> ParsedEmail:
    msg = cast(EmailMessage, BytesParser(policy=policy.default).parsebytes(raw))

    own_ids = _message_ids(str(msg.get("Message-ID", "")))
    from_address, from_name = _first_address(msg)

    plain = _part_text(cast("EmailMessage | None", msg.get_body(preferencelist=("plain",))))
    html = _part_text(cast("EmailMessage | None", msg.get_body(preferencelist=("html",))))
    body_text = plain if plain and plain.strip() else (html_to_text(html) if html else "")

    attachments: list[ParsedAttachment] = []
    for part in msg.iter_attachments():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        data = payload if isinstance(payload, bytes) else b""
        attachments.append(ParsedAttachment(str(filename), part.get_content_type(), data))

    in_reply = _message_ids(str(msg.get("In-Reply-To", "")))
    return ParsedEmail(
        message_id=own_ids[0] if own_ids else synthetic_message_id(raw),
        from_address=from_address,
        from_name=from_name,
        to_addresses=_addresses(msg, "To"),
        cc_addresses=_addresses(msg, "Cc"),
        subject=str(msg.get("Subject", "") or ""),
        date=_parse_date(msg),
        in_reply_to=in_reply[0] if in_reply else None,
        references=_message_ids(" ".join(str(v) for v in msg.get_all("References", []))),
        body_text=body_text,
        body_html=html,
        attachments=attachments,
        headers=[(str(k), str(v)) for k, v in msg.items()],
    )
