"""Общият договор на доставчиците (FCM, HMS) — docs/push.md §2 „Невалиден токен“."""

from dataclasses import dataclass
from typing import Protocol


class InvalidToken(Exception):
    """Доставчикът отхвърли токена (FCM `UNREGISTERED`/`INVALID_ARGUMENT`, HMS `80300007`)."""


@dataclass(frozen=True)
class PushMessage:
    title: str
    body: str
    data: dict[str, str]
    is_urgent: bool
    collapse_key: str | None  # `card-<id>`; за обобщението няма


class PushSender(Protocol):
    def send(self, token: str, message: PushMessage) -> str:
        """Връща id на съобщението; `InvalidToken` при мъртъв токен, друга грешка → `failed`."""
        ...
