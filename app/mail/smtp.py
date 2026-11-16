"""Изходяща поща (docs/architecture.md §5): `build_message` + `send` със `smtplib`."""

import mimetypes
import smtplib
import uuid
from email.message import EmailMessage
from email.utils import format_datetime, formataddr

from app.core import clock
from app.core.config import Settings
from app.models.company import CompanySettings
from app.models.mail import InboundEmail, OutboundEmail

SMTP_TIMEOUT_SECONDS = 30


class SmtpNotConfigured(RuntimeError):
    pass


def message_id_domain(system_address: str) -> str:
    domain = system_address.rpartition("@")[2].strip()
    if not domain:
        raise ValueError(f"system_mailbox_address без домейн: {system_address!r}")
    return domain


def new_message_id(reference_code: str, system_address: str) -> str:
    """`<AIT-0142.<uuid4>@домейн>` — латиница в хедъра, макар кодът да е `АИТ-0142`."""
    number = reference_code.rpartition("-")[2]
    return f"<AIT-{number}.{uuid.uuid4()}@{message_id_domain(system_address)}>"


def subject_with_reference(subject: str, reference_code: str) -> str:
    return subject if f"[{reference_code}]" in subject else f"{subject} [{reference_code}]"


def render_body(body_text: str, signature: str) -> str:
    """Тялото така, както тръгва: текст + подпис. Ползват го и `build_message`, и превюто."""
    return f"{body_text.rstrip()}\n\n-- \n{signature.strip()}\n"


def build_message(
    outbound: OutboundEmail,
    company: CompanySettings,
    *,
    in_reply_to: InboundEmail | None = None,
    attachments: list[tuple[str, str, bytes]] = (),  # type: ignore[assignment]
) -> EmailMessage:
    """Събира писмото. Ако `outbound.message_id` е празен, генерира го и го записва на обекта."""
    if outbound.message_id is None:
        outbound.message_id = new_message_id(outbound.reference_code, company.system_mailbox_address)
    msg = EmailMessage()
    msg["From"] = formataddr((company.short_name, company.system_mailbox_address))
    msg["To"] = ", ".join(outbound.to_addresses)
    if outbound.cc_addresses:
        msg["Cc"] = ", ".join(outbound.cc_addresses)
    msg["Subject"] = subject_with_reference(outbound.subject, outbound.reference_code)
    msg["Date"] = format_datetime(clock.now())
    msg["Message-ID"] = outbound.message_id
    if in_reply_to is not None:
        msg["In-Reply-To"] = in_reply_to.message_id
        msg["References"] = " ".join([*(in_reply_to.references or []), in_reply_to.message_id])
    msg.set_content(render_body(outbound.body_text, company.email_signature))
    if outbound.body_html:
        msg.add_alternative(outbound.body_html, subtype="html")
    for filename, content_type, data in attachments:
        main, _, sub = (
            content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        ).partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=filename)
    return msg


def send(msg: EmailMessage, settings: Settings) -> None:
    if not settings.smtp_host:
        raise SmtpNotConfigured("SMTP не е конфигуриран (APP_SMTP_HOST)")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
        smtp.send_message(msg)
