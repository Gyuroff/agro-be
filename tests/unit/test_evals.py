"""Eval harness (agro-6p5.9): метрики, dataset, baseline, runner върху stub fixtures, запис на fixtures."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda

from app.agents import llm
from evals import baseline, metrics, report
from evals.dataset import load_cases
from evals.runner import aggregate, check_gate, run_eval
from scripts import eval_agents, promote_failure

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _line(d: str, plate: str, qty: str | None, unc: list[str] | None = None) -> dict[str, Any]:
    return {"date": d, "plate_raw": plate, "qty_t": qty, "uncertain_fields": unc or []}


def test_line_metrics_precision_recall_and_qty_error() -> None:
    score = metrics.score_lines(
        [_line("2026-11-19", "СА 1234 ВХ", "25.0"), _line("2026-11-20", "PB2211TT", "25.0")],
        [_line("2026-11-19", "CA1234BX", "25.2"), _line("2026-11-21", "CB7890TP", "25")],
        date_key="date",
        qty_key="qty_t",
        input_text="СА1234ВХ CB7890TP",
        plan_plates=[],
    )
    assert score.counts == {"tp": 1, "fp": 1, "fn": 1}
    assert score.metrics["line_f1"] == 0.5
    assert score.qty_errors == [pytest.approx(0.2)]
    assert score.metrics["no_hallucinated_plates"] == 0.0  # PB2211TT го няма във входа
    assert any("PB2211TT" in p for p in score.problems)


def test_plan_plate_is_not_a_hallucination_and_uncertain_recall() -> None:
    exp = [
        {
            "planned_date": "2026-11-19",
            "plate_norm": "CA1234BX",
            "planned_qty_t": "25",
            "uncertain_fields": ["driver"],
        }
    ]
    ok = metrics.score_lines(
        [_line("2026-11-19", "CA1234BX", "25", ["driver"])], exp, date_key="date", qty_key="qty_t",
        input_text="", plan_plates=["CA 1234 BX"],
    )  # fmt: skip
    assert ok.metrics["no_hallucinated_plates"] == 1.0
    assert ok.metrics["uncertain_recall"] == 1.0
    silent = metrics.score_lines(
        [_line("2026-11-19", "CA1234BX", "25")], exp, date_key="date", qty_key="qty_t",
        input_text="", plan_plates=["CA1234BX"],
    )  # fmt: skip
    assert silent.metrics["uncertain_recall"] == 0.0


def test_score_fields_compares_numbers_as_decimal_and_plates_normalized() -> None:
    out = {"date": "2026-11-12", "plates": ["СА1234ВХ"], "total": "120.0"}
    s = metrics.score_fields(
        out, {"date": "2026-11-12", "plates": ["CA 1234 BX"], "total": "120"}, ["date", "plates", "total"]
    )
    assert s.metrics["field_accuracy"] == 1.0
    bad = metrics.score_fields(out, {"date": "2026-11-13"}, ["date", "plates"])
    assert bad.metrics["field_accuracy"] == 0.0


def test_dataset_loads_expected_json_and_legacy_cases(tmp_path: Path) -> None:
    (tmp_path / "request").mkdir()
    (tmp_path / "request" / "001.expected.json").write_text(
        json.dumps({"agent": "request_parser", "input": {"subject": "s"}, "extract": {"lines": []}}),
        encoding="utf-8",
    )
    (tmp_path / "skeleton.expected.json").write_text(
        json.dumps({"agent": "request_parser", "input": None}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="skeleton"):
        load_cases(tmp_path)
    (tmp_path / "skeleton.expected.json").unlink()
    cases = load_cases(tmp_path)
    assert [c.id for c in cases] == ["request/001"]
    legacy = load_cases(FIXTURES / "classifier", {"email_classifier"})
    assert len(legacy) >= 12 and all(c.agent == "email_classifier" for c in legacy)


def test_baseline_regression_over_three_points(tmp_path: Path) -> None:
    base = {"metrics": {"line_f1": 0.95, "kind_accuracy": 1.0}}
    assert baseline.regressions({"line_f1": 0.93, "kind_accuracy": 1.0}, base) == []
    drop = baseline.regressions({"line_f1": 0.91, "kind_accuracy": 1.0}, base)
    assert len(drop) == 1 and "line_f1" in drop[0]
    assert baseline.regressions({"line_f1": 0.5}, None) == []
    path = baseline.save(
        "x", {"model": "m", "prompt_version": "x@1", "n": 2, "metrics": {"a": 1.0}, "cases": []}, tmp_path
    )
    assert baseline.load("x", tmp_path) == {
        "model": "m",
        "prompt_version": "x@1",
        "n": 2,
        "metrics": {"a": 1.0},
    }
    assert path.name == "x.json"


def test_gate_thresholds() -> None:
    assert check_gate("request_parser", {"schema_valid": 1.0, "line_f1": 0.9, "qty_mae_median": 0.05}) == []
    failed = check_gate("request_parser", {"schema_valid": 0.9, "line_f1": 0.5, "qty_mae_median": 0.2})
    assert len(failed) == 3
    assert aggregate([{"metrics": {}, "counts": {"tp": 1, "fp": 1, "fn": 0}, "qty_errors": [0.1]}]) == {
        "line_f1": pytest.approx(2 / 3),
        "qty_mae_median": 0.1,
    }


def test_run_eval_on_classifier_stub_fixtures() -> None:
    cases = load_cases(FIXTURES / "classifier", {"email_classifier"})
    result = run_eval(cases, ["email_classifier"])
    r = result["agents"]["email_classifier"]
    assert r["model"] == "stub" and r["prompt_version"].startswith("email_classifier@")
    assert r["n"] == len(cases)
    assert r["metrics"]["schema_valid"] == 1.0
    assert "email_classifier" in report.render(result)


def test_cli_refuses_stub_without_flag_and_runs_with_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        eval_agents.main(["--dataset", str(FIXTURES / "classifier"), "--agents", "email_classifier"])
    out = tmp_path / "r.json"
    code = eval_agents.main(
        [
            "--dataset",
            str(FIXTURES / "classifier"),
            "--agents",
            "email_classifier",
            "--allow-stub",
            "--out",
            str(out),
        ]
    )
    assert code in (0, 1)
    assert json.loads(out.read_text(encoding="utf-8"))["agents"]["email_classifier"]["n"] >= 12
    assert "ОБЩ SCORE" in capsys.readouterr().out


def test_recording_fixtures_writes_stub_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.cancellation_parser.schemas import CancellationParserOutput

    class Fake:
        def with_structured_output(self, schema: Any, **_: Any) -> Any:
            return RunnableLambda(lambda _m: schema(date="2026-11-12", plates=["CA1234BX"]))

    monkeypatch.setattr(llm, "get_chat_model", lambda _agent: Fake())
    msgs = [("system", "s"), ("human", "h")]
    with llm.recording_fixtures(tmp_path):
        llm.get_structured_model("cancellation_parser", CancellationParserOutput).invoke(msgs)
    files = list((tmp_path / "cancellation_parser").glob("*.json"))
    assert len(files) == 1
    # същият файл се чете от stub модела
    replay = llm.StubChatModel("cancellation_parser", tmp_path).with_structured_output(
        CancellationParserOutput
    )
    assert replay.invoke(msgs).plates == ["CA1234BX"]
    llm.get_structured_model("cancellation_parser", CancellationParserOutput)  # извън контекста — без запис
    assert llm._record_dir is None  # pyright: ignore[reportPrivateUsage]


def test_promote_failure_skeleton_is_rejected_until_filled() -> None:
    run = SimpleNamespace(
        id=7, agent="request_parser", output={"lines": []}, prompt_version="request_parser@4",
        model="m", status="error", error="boom", input_summary={"subject": "x"},
    )  # fmt: skip
    sk = promote_failure.skeleton(run)  # pyright: ignore[reportArgumentType]
    assert sk["input"] is None and sk["extract"] == {"lines": []} and sk["meta"]["agent_run_id"] == 7
