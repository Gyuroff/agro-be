from typing import Any

PROMPT_VERSION = "mega_invoice_parser@1"
MAX_PDF_CHARS = 12000

SYSTEM = """Ти извличаш данните от ФАКТУРА, издадена в счетоводната система Мега (PDF текст).
Само извличаш — не смяташ и не поправяш нищо; сумите се проверяват отделно.

Върни:
- number: номера на фактурата (ведомите нули се запазват, напр. „0000001524“);
- issue_date: датата на издаване (ISO);
- recipient_eik: ЕИК на ПОЛУЧАТЕЛЯ (клиента), само цифри — НЕ на доставчика/издателя;
- lines: всеки ред {description, qty_t в тонове, unit_price, amount без ДДС, vat_rate в проценти};
- subtotal (данъчна основа), vat_amount (ДДС), total (сума за плащане).

Числата са десетични с точка (1234.50), без интервали и валути. Ако полето го няма — null; НЕ налучквай."""


def render_user(inp: dict[str, Any]) -> str:
    return f"Текст на PDF:\n{str(inp.get('pdf_text', ''))[:MAX_PDF_CHARS]}"


def build_messages(inp: dict[str, Any]) -> list[tuple[str, str]]:
    return [("system", SYSTEM), ("human", render_user(inp))]
