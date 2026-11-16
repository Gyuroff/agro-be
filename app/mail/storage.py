"""Локален запис на писма и прикачени файлове (docs/data-model.md §7, `raw_path`).

Път: `APP_STORAGE_DIR/<kind>/YYYY/MM/<sha256><суфикс>`; атомичен запис (tmp + rename), идемпотентен —
същите байтове в същия месец дават същия път. Датата е от `app.core.clock` (UTC).
"""

import hashlib
import os
import uuid
from pathlib import Path
from typing import NamedTuple

from app.core import clock
from app.core.config import get_settings

_SUFFIX = {"emails": ".eml"}


class SavedBytes(NamedTuple):
    path: str
    sha256: str
    size: int


def save_bytes(kind: str, data: bytes, *, suffix: str | None = None) -> SavedBytes:
    """Записва `data`; връща `(path, sha256, size)`. `kind` е папка (напр. `emails`, `attachments`)."""
    if not kind or "/" in kind or kind.startswith("."):
        raise ValueError(f"невалиден kind: {kind!r}")
    digest = hashlib.sha256(data).hexdigest()
    now = clock.now()
    ext = _SUFFIX.get(kind, "") if suffix is None else suffix
    target = Path(get_settings().storage_dir) / kind / f"{now:%Y}" / f"{now:%m}" / f"{digest}{ext}"
    if not (target.exists() and target.stat().st_size == len(data)):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    return SavedBytes(str(target), digest, len(data))
