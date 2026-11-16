"""Ръчна проверка на истинския Anthropic ключ: `uv run python scripts/llm_smoke.py [agent]`.

Чете `.env` от корена (APP_LLM_PROVIDER=anthropic, APP_LLM_API_KEY=…). Прави едно кратко извикване
със structured output и печата модела, отговора и токените. Не е част от pytest (там е винаги stub).
"""

import sys

from pydantic import BaseModel

from app.agents.llm import get_structured_model, model_id


class Kind(BaseModel):
    kind: str  # request | report | cancellation | mi_invoice | other


def main() -> None:
    agent = sys.argv[1] if len(sys.argv) > 1 else "email_classifier"
    print("модел:", model_id(agent))
    out = get_structured_model(agent, Kind).invoke(
        [
            ("system", "Класифицирай писмото: request, report, cancellation, mi_invoice или other."),
            ("human", "Здравейте, моля за 3 камиона слънчоглед утре сутрин. Поздрави, Иван"),
        ]
    )
    print("отговор:", out)


if __name__ == "__main__":
    main()
