import pytest

from app.domain.packaging import Packaging, normalize_packaging


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("бегове", Packaging.BAGGED),
        ("ББ", Packaging.BAGGED),
        ("биг бегове", Packaging.BAGGED),
        ("пакетаж", Packaging.BAGGED),
        ("опаковани", Packaging.BAGGED),
        ("насипни", Packaging.BULK),
        ("Насипно", Packaging.BULK),
        ("", None),
        (None, None),
        ("нещо друго", None),
    ],
)
def test_normalize(raw: str | None, expected: Packaging | None) -> None:
    assert normalize_packaging(raw) == expected
