"""Offline calibration metrics for explicit assessment evidence."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


VALID_EVENTS = ("correct_answer", "struggled", "forgot")


class CalibrationDataError(ValueError):
    """Raised when a calibration fixture violates its data contract."""


@dataclass(frozen=True)
class CalibrationCase:
    case_id: str
    expected_event: str
    predicted_event: str
    confidence: float

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CalibrationCase":
        case_id = str(payload.get("case_id") or "").strip()
        expected = str(payload.get("expected_event") or "").strip()
        predicted = str(payload.get("predicted_event") or "").strip()
        if not case_id:
            raise CalibrationDataError("calibration case requires case_id")
        if expected not in VALID_EVENTS:
            raise CalibrationDataError(f"invalid expected_event for {case_id}: {expected}")
        if predicted not in VALID_EVENTS:
            raise CalibrationDataError(f"invalid predicted_event for {case_id}: {predicted}")
        try:
            confidence = float(payload["confidence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CalibrationDataError(f"invalid confidence for {case_id}") from exc
        if not 0.0 <= confidence <= 1.0:
            raise CalibrationDataError(f"confidence must be between 0 and 1 for {case_id}")
        return cls(case_id, expected, predicted, confidence)


@dataclass(frozen=True)
class CalibrationReport:
    total: int
    correct: int
    accuracy: float
    mean_confidence: float
    expected_calibration_error: float
    confusion: dict[str, dict[str, int]]

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "mean_confidence": self.mean_confidence,
            "expected_calibration_error": self.expected_calibration_error,
            "confusion": self.confusion,
        }

    def passes(self, *, min_accuracy: float = 0.8, max_ece: float = 0.2) -> bool:
        return self.accuracy >= min_accuracy and self.expected_calibration_error <= max_ece


def load_cases(path: Path) -> list[CalibrationCase]:
    """Load and validate a JSON array of offline assessment cases."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationDataError(f"unable to read calibration fixture: {path}") from exc
    if not isinstance(payload, list):
        raise CalibrationDataError("calibration fixture must be a JSON array")

    cases: list[CalibrationCase] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise CalibrationDataError("each calibration case must be an object")
        case = CalibrationCase.from_dict(item)
        if case.case_id in seen:
            raise CalibrationDataError(f"duplicate calibration case: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise CalibrationDataError("calibration fixture must contain at least one case")
    return cases


def evaluate(cases: Iterable[CalibrationCase], *, bins: int = 10) -> CalibrationReport:
    """Compute accuracy, confidence calibration, and a multiclass confusion matrix."""
    if bins < 1:
        raise ValueError("bins must be positive")
    rows = list(cases)
    if not rows:
        raise CalibrationDataError("at least one calibration case is required")

    correct = sum(case.expected_event == case.predicted_event for case in rows)
    total = len(rows)
    mean_confidence = sum(case.confidence for case in rows) / total
    confusion = {
        expected: {predicted: 0 for predicted in VALID_EVENTS}
        for expected in VALID_EVENTS
    }
    for case in rows:
        confusion[case.expected_event][case.predicted_event] += 1

    # ECE compares confidence with observed accuracy within confidence bins.
    bin_rows: list[list[CalibrationCase]] = [[] for _ in range(bins)]
    for case in rows:
        index = min(int(case.confidence * bins), bins - 1)
        bin_rows[index].append(case)
    ece = 0.0
    for grouped in bin_rows:
        if not grouped:
            continue
        observed = sum(case.expected_event == case.predicted_event for case in grouped) / len(grouped)
        confidence = sum(case.confidence for case in grouped) / len(grouped)
        ece += len(grouped) / total * abs(observed - confidence)

    return CalibrationReport(
        total=total,
        correct=correct,
        accuracy=correct / total,
        mean_confidence=mean_confidence,
        expected_calibration_error=ece,
        confusion=confusion,
    )
