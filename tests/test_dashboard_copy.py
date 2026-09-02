from nexus.dashboard.copy import first_step_copy, freshness_copy, risk_label, why_copy
from nexus.v1_config import V1_NOW


def test_risk_and_reasons_are_plain_language() -> None:
    text = why_copy(
        reasons=("remaining_exceeds_available", "calendar_conflict"),
        remaining=6,
        available=2,
        explanation="6h remaining vs 2h available",
    )
    assert "remaining_exceeds_available" not in text
    assert "还要 6 小时" in text
    assert "能学 2 小时" in text
    assert "日历占掉" in text
    assert risk_label("high") == "时间不够"
    assert risk_label("unscored") == "还算不出风险"


def test_first_step_and_freshness_are_student_facing() -> None:
    assert first_step_copy("CS Assignment 2", 50, "Open CS Assignment 2 and work for 50 minutes") == (
        "打开「CS Assignment 2」，先做 50 分钟。"
    )
    assert "T" not in freshness_copy(V1_NOW)
    assert "刷新于" in freshness_copy(V1_NOW)
