from datetime import date

from freezegun import freeze_time

from app.core import clock


def test_now_is_utc_aware() -> None:
    assert clock.now().tzinfo is not None
    assert clock.now().utcoffset().total_seconds() == 0  # pyright: ignore[reportOptionalMemberAccess]


@freeze_time("2026-11-12 22:30:00")  # 22:30 UTC = 00:30 на 13-и в София (EET, UTC+2)
def test_today_sofia_crosses_midnight() -> None:
    assert clock.today_sofia() == date(2026, 11, 13)
