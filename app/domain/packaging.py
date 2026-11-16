"""Разфасовки (agro-2lg): само две — насипни (`bulk`) и опаковани (`bagged`).

„бегове“, „ББ“, „биг бегове“, „пакетаж“ са синоними на опакованите. Нормализирането е правило в кода,
не в LLM.
"""

import re
from enum import StrEnum


class Packaging(StrEnum):
    BULK = "bulk"
    BAGGED = "bagged"


_BAGGED = re.compile(r"бег|\bбб\b|пакет|опаков|bag", re.IGNORECASE)
_BULK = re.compile(r"насип|bulk|\bна\s*рамп|цистерн", re.IGNORECASE)


def normalize_packaging(raw: str | None) -> Packaging | None:
    """`None`, ако текстът не е разпознат (редът става „избери“)."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if _BAGGED.search(text):
        return Packaging.BAGGED
    if _BULK.search(text):
        return Packaging.BULK
    return None
