from typing import Any

PROMPT_VERSION = "cancellation_parser@1"
MAX_BODY_CHARS = 4000
MAX_XLSX_LINES = 100

SYSTEM = """Ти извличаш ОТКАЗ на курсове от писмо на клиент на зърнотърговец.
Само извличаш — не търсиш курсове и не решаваш нищо.

Върни:
- plates: регистрационните номера на отказаните камиони ТОЧНО както са написани (напр. „СВ 7890 ТР“);
- date: датата на отказаните курсове (ISO). „За петък“, „утре“, „в понеделник“ се смятат спрямо „Днес е“
  (най-близкият такъв ден напред). Ако датата не е ясна, върни null — НЕ я налучквай;
- reason: причината, ако е посочена (кратко, на български), иначе null.

Не добавяй номера, които не са в писмото. Номер от цитирана стара кореспонденция не е отказ."""


def render_user(inp: dict[str, Any]) -> str:
    lines = [f"Днес е: {inp.get('today', '')}", f"От: {inp.get('from_address', '')}"]
    lines += [f"Тема: {inp.get('subject', '')}", "", "Текст:", str(inp.get("body_text", ""))[:MAX_BODY_CHARS]]
    for a in inp.get("attachments") or []:
        if tsv := a.get("tsv"):
            head = "\n".join(tsv.split("\n")[:MAX_XLSX_LINES])
            lines += ["", f"Таблица {a['name']}:", head]
    return "\n".join(lines)


def build_messages(inp: dict[str, Any]) -> list[tuple[str, str]]:
    return [("system", SYSTEM), ("human", render_user(inp))]
