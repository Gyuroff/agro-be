"""Прави stub fixtures за email_classifier от tests/fixtures/classifier/cases.json.

Пускай след всяка промяна на промпта или входа (sha1 на входа се сменя):
`PYTHONPATH=. uv run python scripts/gen_classifier_fixtures.py`.
"""

import json
from pathlib import Path

from app.agents.email_classifier.prompts import build_messages
from app.agents.llm import StubChatModel

CASES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "classifier"


def main() -> None:
    stub = StubChatModel("email_classifier")
    for old in stub.fixtures_dir.joinpath("email_classifier").glob("*.json"):
        old.unlink()
    for case in [
        c for f in sorted(CASES_DIR.glob("*cases.json")) for c in json.loads(f.read_text(encoding="utf-8"))
    ]:
        path = stub.fixture_path(build_messages(case["input"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "kind": case.get("model_kind", case["expected"]),
            "confidence": case["confidence"],
            "reason": case["reason"],
        }
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
