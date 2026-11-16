from typing import Any

PROMPT_VERSION = "email_classifier@1"
MAX_BODY_CHARS = 4000
MAX_XLSX_LINES = 30

SYSTEM = """Ти определяш ВИДА на входящо писмо до зърнотърговец. Връщаш само вид, увереност (0..1) и
причина на български (едно изречение).

Видове:
- request — клиент заявява камиони/количества за дати. Пример: „Заявка за следващата седмица: 3 камиона
  пшеница, понеделник и сряда“; често има таблица със стока, количество, дата, номера на камиони.
- report — справка от доставчик за натоварени/разтоварени курсове (номера, тонове, дати), обикновено xlsx.
  Пример: „Справка за натоварени камиони от склад Русе“.
- cancellation — отказ или отмяна на вече заявени курсове. Пример: „Отказваме курса от четвъртък,
  камионът няма да дойде“.
- mi_invoice — фактура от Microinvest (PDF с номер, контрагент, редове, ДДС). Пример: „Фактура № 0000123“.
- other — всичко останало: реклама, въпроси, благодарности, автоматични известия, неясно съдържание.

Правила:
- Видът се определя по СЪДЪРЖАНИЕТО на писмото и прикачените файлове, не по подателя.
- Ако съдържанието не съвпада ясно с нито един вид — „other“ с ниска увереност.
- Увереността е честна: под 0.7, когато се колебаеш между два вида."""


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def render_user(inp: dict[str, Any]) -> str:
    """Вход: `from_address`, `subject`, `body_text` (изчистен), `attachments=[{name, tsv?}]`."""
    lines = [f"От: {inp.get('from_address', '')}", f"Тема: {inp.get('subject', '')}", ""]
    lines += ["Текст:", _short(str(inp.get("body_text", "")), MAX_BODY_CHARS), ""]
    attachments = inp.get("attachments") or []
    lines.append("Прикачени файлове: " + (", ".join(a["name"] for a in attachments) or "няма"))
    for a in attachments:
        if tsv := a.get("tsv"):
            head = "\n".join(tsv.split("\n")[:MAX_XLSX_LINES])
            lines += ["", f"Първи редове от {a['name']}:", head]
    return "\n".join(lines)


def build_messages(inp: dict[str, Any]) -> list[tuple[str, str]]:
    return [("system", SYSTEM), ("human", render_user(inp))]
