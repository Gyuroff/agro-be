"""Skeleton `expected.json` от изпълнение в `agent_runs` (docs/testing.md §8).

    uv run python scripts/promote_failure.py --agent-run-id 123 [--out-dir tests/fixtures/emails/other]

Човекът попълва `input` (анонимизиран вход на графа) и поправя `extract`/`classify` до истината.
Докато `input` е null, loader-ът отказва файла — случаят не влиза тихо в сета.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import session_scope
from app.models.cards import AgentRun

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "tests" / "fixtures" / "emails" / "failures"


def skeleton(run: AgentRun) -> dict[str, Any]:
    out = run.output or {}
    key = "classify" if run.agent == "email_classifier" else "extract"
    return {
        "id": f"failures/run_{run.id}_{run.agent}",
        "agent": run.agent,
        "meta": {
            "source": "prod_failure",
            "difficulty": "hard",
            "tags": [],
            "notes_bg": "TODO: какво е грешно в изхода на агента",
            "agent_run_id": run.id,
            "prompt_version": run.prompt_version,
            "model": run.model,
            "status": run.status,
            "error": run.error,
        },
        "input": None,
        "input_summary": run.input_summary,
        key: out,
    }


def promote(session: Session, run_id: int, out_dir: Path) -> Path:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise SystemExit(f"Няма agent_run {run_id}")
    path = out_dir / f"run_{run.id}_{run.agent}.expected.json"
    if path.exists():
        raise SystemExit(f"{path} вече съществува — не го презаписвам")
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(skeleton(run), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent-run-id", type=int, required=True)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_DIR)
    args = ap.parse_args()
    with session_scope() as session:
        print(promote(session, args.agent_run_id, args.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
