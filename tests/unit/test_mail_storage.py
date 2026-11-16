import hashlib
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.mail.storage import save_bytes


@pytest.fixture(autouse=True)
def _storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()


def test_same_bytes_same_sha_and_path() -> None:
    data = "Здравейте\r\n".encode()
    first, second = save_bytes("emails", data), save_bytes("emails", data)
    assert first == second
    assert first.sha256 == hashlib.sha256(data).hexdigest()
    assert first.size == len(data)
    assert Path(first.path).read_bytes() == data
    assert first.path.endswith(f"/{first.sha256}.eml")


def test_path_layout_and_no_tmp_left(tmp_path: Path) -> None:
    saved = save_bytes("attachments", b"x", suffix=".xlsx")
    rel = Path(saved.path).relative_to(tmp_path)
    assert rel.parts[0] == "attachments" and len(rel.parts) == 4 and rel.suffix == ".xlsx"
    assert [p for p in tmp_path.rglob("*") if p.name.endswith(".tmp")] == []


def test_invalid_kind() -> None:
    with pytest.raises(ValueError):
        save_bytes("../x", b"1")
