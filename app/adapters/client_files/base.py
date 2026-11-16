"""Адаптер към файла на конкретен клиент (agro-cmf).

Всеки клиент има свой файл със свои правила (един попълва „Място на товарене“ в колона, друг — ред по ред
за всеки камион, трети изобщо не връща файл). Затова няма универсален код: общото е само интерфейсът,
а правилата живеят в адаптер за клиента (`ameta.py`). Контрагентът пази само ИМЕТО на адаптера
(`counterparties.client_file_adapter`); NULL = не връщаме файл.

Адаптерът НЕ налучква: каквото не съвпада с очакванията му, връща като предупреждение и не пипа файла.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class LineAssignment:
    """Какво знаем за един ред на заявката — адаптерът взима от него каквото му трябва."""

    source_row: int | None  # ред в оригиналния файл (1-based), ако агентът го е върнал
    planned_date: date
    plate: str
    driver_name: str | None
    qty_t: Decimal
    own_transport: bool
    warehouse_name: str | None
    supplier_name: str | None


@dataclass
class FillResult:
    data: bytes | None = None  # None → файл не се връща
    filled: int = 0
    warnings: list[str] = field(default_factory=list)


class ClientFileAdapter(Protocol):
    key: str
    label: str  # как се вижда в падащото меню в настройките на клиента
    filename_suffix: str  # напр. ".xlsx"

    def fill(self, path: str | Path, lines: list[LineAssignment]) -> FillResult:
        """Копие на оригиналния файл с попълнено за `lines`; `data=None`, ако не може да се попълни."""
        ...
