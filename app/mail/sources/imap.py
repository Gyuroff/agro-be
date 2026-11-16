"""IMAP източник (architecture §5): INBOX, UNSEEN, `\\Seen` само след успешен ingest."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from imapclient import IMAPClient

from app.core.config import Settings

log = logging.getLogger(__name__)

SEEN = b"\\Seen"
TIMEOUT_SECONDS = 30


class ImapNotConfigured(RuntimeError):
    pass


@dataclass
class PollResult:
    fetched: int = 0
    errors: list[str] = field(default_factory=list)


def poll_imap(settings: Settings, handle: Callable[[bytes], object]) -> PollResult:
    """Чете непрочетените писма и вика `handle(raw)` за всяко; `\\Seen` само ако `handle` не хвърли.

    Грешка на едно писмо не спира останалите (то остава непрочетено и се опитва пак на следващия poll).
    Грешка на връзката/входа излиза като изключение.
    """
    if not (settings.imap_host and settings.imap_user and settings.imap_password):
        raise ImapNotConfigured("IMAP не е конфигуриран (APP_IMAP_HOST/USER/PASSWORD)")
    result = PollResult()
    with IMAPClient(
        settings.imap_host, port=settings.imap_port, ssl=settings.imap_ssl, timeout=TIMEOUT_SECONDS
    ) as client:
        client.login(settings.imap_user, settings.imap_password.get_secret_value())
        client.select_folder("INBOX")
        for uid in client.search("UNSEEN"):
            try:
                data = client.fetch([uid], ["BODY.PEEK[]"])  # PEEK: сървърът не слага \Seen сам
                handle(cast(bytes, data[uid][b"BODY[]"]))
                client.add_flags([uid], [SEEN])
            except Exception as exc:
                log.exception("писмо uid=%s не е прието", uid)
                result.errors.append(f"uid {uid}: {exc}")
            else:
                result.fetched += 1
    return result
