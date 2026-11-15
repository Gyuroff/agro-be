"""Регистрационни номера (docs/data-model.md §8.1): нормализиране, проверка, показване."""

import re

# Кирилица → латиница за еднаквите на вид букви.
_CYR_TO_LAT = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
_LAT_TO_CYR = str.maketrans("ABEKMHOPCTYX", "АВЕКМНОРСТУХ")
_STRIP = re.compile(r"[\s\-.]+")
_PLATE = re.compile(r"^([A-Z]{1,2})([0-9]{4})([A-Z]{1,2})$")


def normalize_plate(raw: str) -> str:
    """`upper()`, без интервали/тирета/точки, кирилица → латиница. Невалидният се връща такъв, какъвто е."""
    return _STRIP.sub("", raw.upper()).translate(_CYR_TO_LAT)


def is_valid_plate(raw: str) -> bool:
    return _PLATE.match(normalize_plate(raw)) is not None


def format_plate(raw: str) -> str:
    """`CA1234BX` → `СА 1234 ВХ`. Невалиден номер се връща нормализиран, без форматиране."""
    norm = normalize_plate(raw)
    match = _PLATE.match(norm)
    if match is None:
        return norm
    return " ".join(part.translate(_LAT_TO_CYR) for part in match.groups())
