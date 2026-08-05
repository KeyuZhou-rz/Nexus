from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.cli import calibration
from nexus.knowledge.calibration import CalibrationCase, CalibrationDataError, evaluate, load_cases


FIXTURE = Path(__file__).parent / "fixtures" / "mastery_calibration.json"


def test_fixture_produces_a_passing_calibration_report() -> None:
    report = evaluate(load_cases(FIXTURE))

    assert report.total == 8
    assert report.correct == 7
    assert report.accuracy == pytest.approx(0.875)
    assert report.expected_calibration_error < 0.2
    assert report.confusion["correct_answer"]["correct_answer"] == 2


def test_calibration_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        json.dumps([
            {"case_id": "same", "expected_event": "forgot", "predicted_event": "forgot", "confidence": 0.8},
            {"case_id": "same", "expected_event": "forgot", "predicted_event": "forgot", "confidence": 0.8},
        ]),
        encoding="utf-8",
    )

    with pytest.raises(CalibrationDataError, match="duplicate"):
        load_cases(path)


def test_calibration_cli_emits_gate_result(capsys) -> None:
    assert calibration.main(["--fixture", str(FIXTURE), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["report"]["total"] == 8


def test_calibration_case_rejects_out_of_range_confidence() -> None:
    with pytest.raises(CalibrationDataError, match="between 0 and 1"):
        CalibrationCase.from_dict({
            "case_id": "bad",
            "expected_event": "forgot",
            "predicted_event": "forgot",
            "confidence": 1.1,
        })
