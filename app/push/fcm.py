"""FCM през firebase-admin (docs/push.md §2–§3): android priority/tag/channel, apns headers, data."""

import logging
from functools import lru_cache

import firebase_admin
from firebase_admin import credentials, exceptions, messaging

from app.core.config import Settings
from app.push.sender import InvalidToken, PushMessage

log = logging.getLogger(__name__)

DEAD_TOKEN_ERRORS = (
    messaging.UnregisteredError,
    messaging.SenderIdMismatchError,
    exceptions.InvalidArgumentError,
)
ANDROID_CHANNEL = "actions"  # „Изисква действие“ — създава го приложението


def build_message(token: str, message: PushMessage) -> messaging.Message:
    high = message.is_urgent
    return messaging.Message(
        token=token,
        notification=messaging.Notification(title=message.title, body=message.body),
        data=message.data,
        android=messaging.AndroidConfig(
            priority="high" if high else "normal",
            collapse_key=message.collapse_key,
            notification=messaging.AndroidNotification(tag=message.collapse_key, channel_id=ANDROID_CHANNEL),
        ),
        apns=messaging.APNSConfig(
            headers={
                "apns-priority": "10" if high else "5",
                **({"apns-collapse-id": message.collapse_key} if message.collapse_key else {}),
            }
        ),
    )


@lru_cache(maxsize=1)
def _app(credentials_file: str) -> firebase_admin.App:
    return firebase_admin.initialize_app(credentials.Certificate(credentials_file))


class FcmSender:
    def __init__(self, credentials_file: str) -> None:
        self._credentials_file = credentials_file

    @classmethod
    def from_settings(cls, settings: Settings) -> "FcmSender | None":
        return cls(settings.fcm_credentials_file) if settings.fcm_credentials_file else None

    def send(self, token: str, message: PushMessage) -> str:
        app = _app(self._credentials_file)
        response = messaging.send_each([build_message(token, message)], app=app).responses[0]
        if response.success:
            return str(response.message_id)
        exc = response.exception
        if isinstance(exc, DEAD_TOKEN_ERRORS):
            raise InvalidToken(str(exc)) from exc
        raise RuntimeError(str(exc)) from exc
