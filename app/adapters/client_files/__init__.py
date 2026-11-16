"""Регистър на адаптерите към клиентските файлове (ключът е в `counterparties.client_file_adapter`)."""

from app.adapters.client_files.ameta import AmetaAdapter
from app.adapters.client_files.base import ClientFileAdapter, FillResult, LineAssignment

ADAPTERS: dict[str, ClientFileAdapter] = {a.key: a for a in (AmetaAdapter(),)}


def adapter_choices() -> list[tuple[str, str]]:
    """`[(ключ, етикет)]` за менюто „Файл към клиента“ в настройките; празният избор = без файл."""
    return [(a.key, a.label) for a in ADAPTERS.values()]


def get_adapter(key: str | None) -> ClientFileAdapter | None:
    """`None` за липсващ ключ; непознат ключ е грешка в настройката (не се прикрива)."""
    if key is None:
        return None
    try:
        return ADAPTERS[key]
    except KeyError:
        raise ValueError(f"непознат адаптер за клиентски файл: {key!r}") from None


__all__ = ["ADAPTERS", "ClientFileAdapter", "FillResult", "LineAssignment", "adapter_choices", "get_adapter"]
