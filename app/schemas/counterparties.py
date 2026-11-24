"""Контрагенти в JSON (docs/api.md §5, sheet 7)."""

from pydantic import BaseModel


class CounterpartySearchItemDTO(BaseModel):
    id: int
    name: str
    role_label: str
    suggested: bool
