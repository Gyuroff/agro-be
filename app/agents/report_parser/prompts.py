from typing import Any

PROMPT_VERSION = "report_parser@1"
MAX_BODY_CHARS = 4000
MAX_XLSX_LINES = 300

SYSTEM = """Ти извличаш изпълнените курсове от СПРАВКА на доставчик на зърнотърговец (текст и/или xlsx).
Само извличаш — не съпоставяш с плана и не решаваш нищо.

За всеки курс (ред на справката) върни: load_date (ISO), plate_raw (регистрационен номер ТОЧНО както е
написан), driver_name (ако има), qty_t (тонове), location_raw („Място на товарене“, ако има колона) и
source_row.

Правила:
- qty_t е ВИНАГИ в тонове. Ако таблицата е в килограми (напр. 24860), върни 24.86. Без „т“/„кг“.
- Ако в реда няма тонаж, qty_t е null и "qty" е в uncertain_fields. НЕ го измисляй и не го вземай от друг ред.
- Ако няма номер, plate_raw е "" и "plate" е в uncertain_fields.
- Редове с общо/сума, заглавия и празни редове НЕ са курсове.
- В таблицата всеки ред започва с [rN]; source_row = N (само числото).
- Годината на датата е тази от писмото, ако не е написана.
- Нечетливо или двусмислено поле не се налучква: остави го празно и го сложи в uncertain_fields
  (plate, date, driver, qty); причината — в uncertain_note (едно изречение на български).
- Не добавяй редове, които не са в справката."""


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
