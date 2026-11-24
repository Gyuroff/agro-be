"""Gate на agro-ha8.7: M-I1…M-I10 (docs/testing.md §3.2) са покрити и всички се пускат с `pytest -k mail`.

Сценариите живеят до кода, който проверяват; тук е картата M-I → тест. Мета-тестът пази картата от
разсъхване: преименуван/изтрит тест или модул без маркер `mail` го чупи.
"""

import importlib

import pytest

pytestmark = pytest.mark.mail

T = "tests.integration."
COVERAGE: dict[str, list[tuple[str, str]]] = {
    "M-I1": [(T + "test_mailbox", "test_poll_ingests_and_marks_seen")],
    "M-I2": [(T + "test_mail_ingest", "test_duplicate_returns_none_without_second_row")],
    "M-I3": [(T + "test_mail_process", "test_unverified_sender_card_without_agent")],
    "M-I4": [(T + "test_request_intake", "test_figma_request_12_trucks")],
    "M-I5": [
        (T + "test_mail_process", "test_unknown_sender_card_with_domain_suggestion"),
        (T + "test_unknown_sender", "test_link_remember_creates_learned_contact_and_reprocesses"),
        (T + "test_unknown_sender", "test_next_mail_from_same_address_matches_by_contact"),
    ],
    "M-I6": [
        (T + "test_report_intake", "test_figma_report_11_1_1_with_substitute"),
        (T + "test_report_matching", "test_suggest_substitutes_links_rv2211tt_to_sv7890tr"),
    ],
    "M-I7": [(T + "test_requests", "test_send_then_undo_restores_everything")],
    "M-I8": [(T + "test_requests", "test_task_creates_trips_and_sends")],
    "M-I9": [
        (T + "test_mail_send", "test_smtp_error_marks_failed_reopens_card_and_notifies"),
        (T + "test_requests", "test_smtp_error_rolls_back_trips_and_reopens_card"),
        (T + "test_push", "test_send_failed_hook_pushes_urgent_p4"),
    ],
    "M-I10": [(T + "test_mailbox", "test_watchdog_one_card_then_superseded_on_ok")],
}


def _marks(obj: object) -> list[pytest.Mark]:
    marks = getattr(obj, "pytestmark", [])
    return marks if isinstance(marks, list) else [marks]


def test_every_scenario_is_mapped() -> None:
    assert list(COVERAGE) == [f"M-I{i}" for i in range(1, 11)]


@pytest.mark.parametrize(("scenario", "target"), [(s, t) for s, ts in COVERAGE.items() for t in ts])
def test_target_exists_and_is_selected_by_mail(scenario: str, target: tuple[str, str]) -> None:
    module_name, test_name = target
    module = importlib.import_module(module_name)
    assert callable(getattr(module, test_name, None)), f"{scenario}: няма {module_name}::{test_name}"
    marks = [*_marks(module), *_marks(getattr(module, test_name))]
    assert "mail" in module_name or any(m.name == "mail" for m in marks), (
        f"{scenario}: {module_name} без маркер mail (модул или тест)"
    )
