"""Импортът на `app.db` регистрира одитните слушатели (docs/data-model.md §2.3)."""

from app.db import audit as _audit  # noqa: F401  # регистрира before_flush / after_flush
