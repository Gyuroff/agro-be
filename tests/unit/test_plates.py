import pytest

from app.domain.plates import format_plate, is_valid_plate, normalize_plate


@pytest.mark.parametrize("raw", ["СА 1234 ВХ", "CA1234BX", "са-1234-вх", " ca.1234.bx ", "CA 1234 BX"])
def test_normalize_equivalent_spellings(raw: str) -> None:
    assert normalize_plate(raw) == "CA1234BX"


def test_format_plate() -> None:
    assert format_plate("CA1234BX") == "СА 1234 ВХ"
    assert format_plate("са-1234-вх") == "СА 1234 ВХ"
    assert format_plate("B1234KT") == "В 1234 КТ"
    assert format_plate("СА1234ВХ") == "СА 1234 ВХ"


def test_format_invalid_returns_normalized() -> None:
    assert format_plate("ab-12") == "AB12"


@pytest.mark.parametrize("raw", ["СА 1234 ВХ", "CA1234BX", "B1234K", "са-1234-вх"])
def test_valid(raw: str) -> None:
    assert is_valid_plate(raw)


@pytest.mark.parametrize(
    "raw", ["", "AB12", "CA12345BX", "CAA1234BX", "CA1234", "1234", "СА 1234 БХ", "CA1234BXX"]
)
def test_invalid(raw: str) -> None:
    assert not is_valid_plate(raw)
