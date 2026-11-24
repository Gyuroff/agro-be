"""Зарежда golden случаите (docs/testing.md §2.1).

Два източника под `--dataset`:
* `**/*.expected.json` — по един случай: `id`, `agent`, `meta`, `input` (вход на графа, както го строи
  intake: `from_address`, `subject`, `body_text`, `attachments`…), очаквано в `classify`/`extract`;
* `**/*cases.json` — старият формат на класификатора (`tests/fixtures/classifier`): списък с
  `name`, `expected`, `doc_kind`, `reply_status`, `input`. Случаите с `pending_spec` се пропускат.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CLASSIFIER = "email_classifier"


@dataclass(frozen=True)
class Case:
    id: str
    agent: str
    input: dict[str, Any]
    expected: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)
    plan_plates: list[str] = field(
        default_factory=list
    )  # номера от плана — допустими, дори да не са във входа


def _from_expected(path: Path, root: Path) -> Case:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("input") is None:
        raise ValueError(f"{path}: няма `input` (skeleton от promote_failure — попълни го)")
    expected = {**raw.get("classify", {}), **raw.get("extract", {})}
    expected["_tolerances"] = raw.get("tolerances", {})
    return Case(
        id=raw.get("id") or path.relative_to(root).as_posix().removesuffix(".expected.json"),
        agent=raw["agent"],
        input=raw["input"],
        expected=expected,
        meta=raw.get("meta", {}),
        plan_plates=raw.get("plan_plates", []),
    )


def _from_classifier_cases(path: Path) -> list[Case]:
    out: list[Case] = []
    for c in json.loads(path.read_text(encoding="utf-8")):
        if c.get("pending_spec"):
            continue
        meta = {"source": path.name, "label_verified": c.get("label_verified", True)}
        expected = {
            "kind": c["expected"],
            "doc_kind": c.get("doc_kind"),
            "reply_status": c.get("reply_status"),
        }
        out.append(Case(f"{path.stem}/{c['name']}", CLASSIFIER, c["input"], expected, meta))
    return out


def load_cases(dataset: Path, agents: set[str] | None = None) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(dataset.rglob("*.expected.json")):
        cases.append(_from_expected(path, dataset))
    for path in sorted(dataset.rglob("*cases.json")):
        cases.extend(_from_classifier_cases(path))
    return [c for c in cases if agents is None or c.agent in agents]
