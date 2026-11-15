"""AGENTS.md: без `session.execute(update(...))` / `delete(...)` върху модели — заобикалят одита.

Грепва `app/` (AST, не текст) за sqlalchemy `update`/`delete`. Масови промени → `record_bulk`.
Позволено е само в `app/db/audit.py`.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
ALLOWED = {APP / "db" / "audit.py"}
FORBIDDEN = {"update", "delete"}


def violations(source: str) -> list[int]:
    """Номерата на редовете с sqlalchemy update()/delete() в даден изходен код."""
    tree = ast.parse(source)
    bare: set[str] = set()  # имена, внесени с `from sqlalchemy import update [as u]`
    modules: set[str] = set()  # псевдоними на sqlalchemy (import sqlalchemy as sa)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "sqlalchemy":
            bare |= {a.asname or a.name for a in node.names if a.name in FORBIDDEN}
        elif isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name.split(".")[0] == "sqlalchemy"}
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id in bare) or (
            isinstance(func, ast.Attribute)
            and func.attr in FORBIDDEN
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        ):
            lines.append(node.lineno)
    return lines


def test_app_has_no_bulk_update_or_delete() -> None:
    found = {
        f"{path.relative_to(APP.parent)}:{line}"
        for path in APP.rglob("*.py")
        if path not in ALLOWED
        for line in violations(path.read_text(encoding="utf-8"))
    }
    assert not found, f"update()/delete() заобикалят одита — ползвай ORM или record_bulk: {sorted(found)}"


def test_detector_catches_all_import_styles() -> None:
    assert violations("from sqlalchemy import update\nupdate(M)") == [2]
    assert violations("from sqlalchemy import delete as d\nd(M)") == [2]
    assert violations("import sqlalchemy as sa\nsa.delete(M)") == [2]
    assert violations("import sqlalchemy\nsqlalchemy.update(M)") == [2]
    assert violations("d = {}\nd.update(x=1)\nsession.delete(obj)") == []
