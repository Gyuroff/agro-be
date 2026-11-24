"""FCM: форма на съобщението и картографиране на грешките (docs/push.md §2–§3); firebase-admin е мокнат."""

from types import SimpleNamespace
from typing import Any

import pytest
from firebase_admin import exceptions, messaging

from app.push import fcm
from app.push.sender import InvalidToken, PushMessage


def msg(urgent: bool) -> PushMessage:
    return PushMessage(
        title="T",
        body="B",
        data={"kind": "cancellation", "card_id": "142", "route": "/dashboard?card=142"},
        is_urgent=urgent,
        collapse_key="card-142",
    )


def test_urgent_message_headers() -> None:
    m = fcm.build_message("tok", msg(True))
    assert m.android.priority == "high" and m.android.collapse_key == "card-142"  # pyright: ignore[reportOptionalMemberAccess]
    assert m.android.notification.tag == "card-142" and m.android.notification.channel_id == "actions"  # pyright: ignore[reportOptionalMemberAccess]
    assert m.apns.headers == {"apns-priority": "10", "apns-collapse-id": "card-142"}  # pyright: ignore[reportOptionalMemberAccess]
    assert m.data == {"kind": "cancellation", "card_id": "142", "route": "/dashboard?card=142"}


def test_normal_message_priority() -> None:
    m = fcm.build_message("tok", msg(False))
    assert m.android.priority == "normal" and m.apns.headers["apns-priority"] == "5"  # pyright: ignore[reportOptionalMemberAccess]


def _patch(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    monkeypatch.setattr(fcm, "_app", lambda _f: object())
    monkeypatch.setattr(messaging, "send_each", lambda _msgs, app=None: SimpleNamespace(responses=[response]))


def test_success_returns_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, SimpleNamespace(success=True, message_id="projects/x/messages/1", exception=None))
    assert fcm.FcmSender("f.json").send("tok", msg(False)) == "projects/x/messages/1"


@pytest.mark.parametrize(
    "exc", [messaging.UnregisteredError("gone", None, None), exceptions.InvalidArgumentError("bad token")]
)
def test_dead_token_errors_map_to_invalid_token(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    _patch(monkeypatch, SimpleNamespace(success=False, message_id=None, exception=exc))
    with pytest.raises(InvalidToken):
        fcm.FcmSender("f.json").send("tok", msg(False))


def test_other_error_is_plain_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        SimpleNamespace(success=False, message_id=None, exception=exceptions.UnavailableError("x")),
    )
    with pytest.raises(RuntimeError):
        fcm.FcmSender("f.json").send("tok", msg(False))
