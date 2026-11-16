from pathlib import Path

import pytest

from app.mail.auth_results import AuthResult, parse_authentication_results
from app.mail.parse import extract_reference_code, html_to_text, parse

FIX = Path(__file__).parent.parent / "fixtures" / "emails" / "synthetic"


def _load(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_cyrillic_subject_and_sender() -> None:
    p = parse(_load("cyrillic_subject.eml"))
    assert p.subject == "Re: Заявка за камиони [АИТ-142]"
    assert (p.from_address, p.from_name) == ("maria@agrotrade.example", "Мария Георгиева")
    assert p.to_addresses == ["orders@agroimport.example", "two@agroimport.example"]
    assert p.cc_addresses == ["cc@agrotrade.example"]
    assert p.message_id == "<m1@agrotrade.example>"
    assert p.in_reply_to == "<AIT-0142.x@agroimport.example>"
    assert p.references == ["<a@x>", "<AIT-0142.x@agroimport.example>"]
    assert p.date is not None and p.date.utcoffset() is not None
    assert "Потвърждавам" in p.body_text
    assert extract_reference_code(p.subject) == "АИТ-0142"


def test_multipart_attachment_with_cyrillic_name() -> None:
    p = parse(_load("multipart_xlsx.eml"))
    assert "Приложено" in p.body_text
    [att] = p.attachments
    assert att.filename == "Камиони_седмица_47.xlsx"
    assert att.content_type.endswith("spreadsheetml.sheet")
    assert att.data == b"PK\x03\x04fake-xlsx"


def test_html_only_becomes_text() -> None:
    p = parse(_load("html_only.eml"))
    assert p.body_html and "<b>утре</b>" in p.body_html
    assert p.body_text == "Здравейте\nТоварим утре"


def test_missing_message_id_is_synthetic_sha() -> None:
    raw = _load("no_message_id.eml")
    p = parse(raw)
    assert p.message_id.endswith("@local>") and len(p.message_id) == len("<@local>") + 64
    assert parse(raw).message_id == p.message_id


def test_broken_input_does_not_raise() -> None:
    p = parse(b"Subject: \xff\xfe\r\n\r\n" + "просто текст".encode())
    assert p.from_address == "" and p.message_id.endswith("@local>")


@pytest.mark.parametrize(
    ("subject", "code"),
    [
        ("[АИТ-0142] Курсове", "АИТ-0142"),
        ("Re: [AIT-7] x", "АИТ-0007"),
        ("[ait-12345]", "АИТ-12345"),
        ("без код", None),
        ("АИТ-0142 без скоби", None),
        (None, None),
    ],
)
def test_reference_code(subject: str | None, code: str | None) -> None:
    assert extract_reference_code(subject) == code


def test_html_to_text_skips_script() -> None:
    assert html_to_text("<script>x()</script><div>а</div><div>б</div>") == "а\nб"


def _hdr(value: str) -> list[tuple[str, str]]:
    return [("Authentication-Results", value)]


@pytest.mark.parametrize(
    ("value", "expected", "ok"),
    [
        ("mx.test; spf=pass; dkim=pass; dmarc=pass", AuthResult("pass", "pass", "pass"), True),
        ("mx.test; spf=pass; dkim=none; dmarc=none", AuthResult("pass", "none", "none"), True),
        ("mx.test; spf=softfail; dkim=pass; dmarc=pass", AuthResult("softfail", "pass", "pass"), True),
        ("mx.test; spf=pass; dkim=pass; dmarc=fail", AuthResult("pass", "pass", "fail"), False),
        ("mx.test; spf=fail; dkim=none; dmarc=fail", AuthResult("fail", "none", "fail"), False),
        ("mx.test; spf=softfail; dkim=none", AuthResult("softfail", "none", None), False),
        ("mx.test; spf=pass", AuthResult("pass", None, None), True),
        ("MX.Test 1; spf=pass (ok); dmarc=pass (p=NONE)", AuthResult("pass", None, "pass"), True),
    ],
)
def test_auth_matrix(value: str, expected: AuthResult, ok: bool) -> None:
    result = parse_authentication_results(_hdr(value), "mx.test")
    assert result == expected and result.auth_ok is ok


def test_foreign_authserv_id_is_ignored() -> None:
    result = parse_authentication_results(_hdr("evil.example; spf=pass; dkim=pass; dmarc=pass"), "mx.test")
    assert result == AuthResult() and result.auth_ok is False


def test_trusted_header_wins_over_forged_one() -> None:
    headers = [*_hdr("mx.test; spf=fail; dmarc=fail"), *_hdr("evil.example; spf=pass; dkim=pass")]
    assert parse_authentication_results(headers, "mx.test").auth_ok is False


def test_no_configured_authserv_trusts_nothing() -> None:
    assert parse_authentication_results(_hdr("mx.test; spf=pass"), None).auth_ok is False


def test_fixtures_auth() -> None:
    def ok(name: str) -> bool:
        return parse_authentication_results(parse(_load(name)).headers, "mx.test").auth_ok

    assert (ok("spf_fail.eml"), ok("foreign_authserv.eml"), ok("multipart_xlsx.eml")) == (False, False, True)
