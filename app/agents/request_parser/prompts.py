from typing import Any

PROMPT_VERSION = "request_parser@4"
MAX_BODY_CHARS = 6000
MAX_XLSX_LINES = 200

SYSTEM = """Ти извличаш КАМИОНИТЕ от заявка на клиент на зърнотърговец (текст на писмото и/или xlsx таблица).
Само извличаш — не решаваш договор, цена или статус.

За всеки камион върни ред: дата (ISO), регистрационен номер ТОЧНО както е написан (plate_raw), по желание
ремарке, шофьор, телефон на шофьора, количество в тонове (qty_t; null, ако не е посочено — НЕ го измисляй).
Върни и period_from/period_to (ако са ясни), product_hint (стока, ако е спомената), warehouse_hint (склад/
място на товарене, ако е спомената) и кратки notes.

Правила за полетата:
- qty_t е ВИНАГИ в тонове. Ако таблицата е в килограми (напр. 21820), върни 21.82. Не пиши „т“ или „кг“.
- Ако няма регистрационен номер (в текстови заявки често има само дата, вид автомобил и количество),
  plate_raw е празен низ "" и "plate" е в uncertain_fields. Никога не пиши „не е посочен“ или шаблон.
- Две регистрации в едно поле, разделени с „/“ или „+“ → първата е plate_raw, втората — trailer_plate.
- packaging_raw: разфасовката на реда ТОЧНО както е написана („насипни“, „бегове“, „ББ“, „пакетаж“…), без да я
  превеждаш; location_raw: „Място на товарене“ ТОЧНО както е написано. Няма ли ги — null.
- В таблицата всеки ред започва с [rN]. source_row = N на реда, от който е взет камионът (САМО числото, без
  „r“); за текстови заявки без таблица — null. Не налучквай.
- own_transport = true само за ред, в който пише „транспорт Ива“ (наш камион): тогава plate_raw е "".
- Дата: използвай „Дата на изпълнение“; годината е тази от писмото, ако не е написана.

Честност: в uncertain_fields на реда сложи кодовете на полетата, в които не си сигурен:
plate, date, driver, qty, trailer, driver_phone. Причината — в uncertain_note (едно изречение на български).
Нечетливо, зачертано или двусмислено поле НЕ се налучква: остави го празно/сложи го в uncertain_fields.
Не добавяй редове, които не са в писмото."""


def render_user(inp: dict[str, Any]) -> str:
    """Вход: `from_address`, `subject`, `body_text` (изчистен), `attachments=[{name, tsv?}]`, `today`."""
    lines = [f"Днес е: {inp.get('today', '')}", f"От: {inp.get('from_address', '')}"]
    lines += [f"Тема: {inp.get('subject', '')}", "", "Текст:", str(inp.get("body_text", ""))[:MAX_BODY_CHARS]]
    for a in inp.get("attachments") or []:
        if tsv := a.get("tsv"):
            head = "\n".join(tsv.split("\n")[:MAX_XLSX_LINES])
            lines += ["", f"Таблица {a['name']}:", head]
    return "\n".join(lines)


def build_messages(inp: dict[str, Any]) -> list[tuple[str, str]]:
    return [("system", SYSTEM), ("human", render_user(inp))]
