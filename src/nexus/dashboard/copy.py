"""Student-facing Today copy. Machine codes stay in the engine."""
from __future__ import annotations

from datetime import datetime

from ..utils import localize_dt

WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

RISK_LABELS = {
    "overdue": "已过期",
    "high": "时间不够",
    "medium": "这几天要赶",
    "low": "还来得及",
    "unscored": "还算不出风险",
}

REASON_LABELS = {
    "overdue": "已经过了截止时间",
    "remaining_exceeds_available": "剩下的工作比能学的时间多",
    "deadline_within_24h": "截止不到 24 小时",
    "deadline_within_72h": "三天内就要交",
    "load_ratio_high": "剩下的时间里学习负担偏重",
    "load_ratio_medium": "这周学习负担不轻",
    "calendar_conflict": "日历占掉了一部分能学的时间",
    "availability_unknown": "还不知道这周什么时候能学",
    "no_deadline": "还没有截止日期",
    "no_remaining_estimate": "还不知道还要做多久",
    "within_horizon": "还在这轮规划里",
    "inactive": "已经做完了",
}


def freshness_copy(now: datetime) -> str:
    local = now if now.tzinfo else now.astimezone()
    return f"刷新于 {local.strftime('%H:%M')}"


def risk_label(band: str | None) -> str:
    if not band:
        return RISK_LABELS["unscored"]
    return RISK_LABELS.get(band, band)


def reason_label(code: str) -> str:
    return REASON_LABELS.get(code, code)


def hours_copy(remaining: float | None, available: float | None) -> str:
    parts: list[str] = []
    if remaining is not None:
        parts.append(f"还要 {remaining:g} 小时")
    if available is not None:
        parts.append(f"截止前能学 {available:g} 小时")
    return "，".join(parts)


def why_copy(
    *,
    reasons: tuple[str, ...] | list[str] | None,
    remaining: float | None,
    available: float | None,
    explanation: str | None = None,
) -> str:
    hours = hours_copy(remaining, available)
    labels = [reason_label(code) for code in (reasons or ()) if code]
    # Drop machine-looking leftovers and English engine sentences.
    skip = {
        "availability_unknown",
        "Add when you can study this week to calculate risk",
    }
    if explanation and explanation not in skip and "remaining vs" not in explanation:
        if explanation not in labels and explanation != hours:
            labels.append(explanation)
    parts = [part for part in (hours, *labels) if part]
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return "。".join(seen)


def first_step_copy(title: str, block_minutes: int, raw: str | None = None) -> str:
    if raw and not raw.startswith("Open ") and "work for" not in raw:
        if raw.startswith("Add ") or raw.startswith("Nothing "):
            return "先加上作业、截止，以及这周能学的时间。"
        if "study this week" in raw.lower():
            return "先补上这周能学的时间，才能判断做不做得完。"
        return raw
    if title and block_minutes:
        return f"打开「{title}」，先做 {block_minutes} 分钟。"
    if title:
        return f"打开「{title}」开始做。"
    return "先加上一条功课，Nexus 会告诉你下一步。"


def deadline_copy(due, now_local: datetime) -> str:
    if due is None:
        return ""
    local = localize_dt(due, now_local) or due
    if local.tzinfo is None and now_local.tzinfo is not None:
        local = local.replace(tzinfo=now_local.tzinfo)
    if local < now_local:
        return "已经过了截止时间"
    days = (local.date() - now_local.date()).days
    if days == 0:
        return "今天截止"
    if days == 1:
        return "明天截止"
    return f"{WEEKDAYS_ZH[local.weekday()]}截止"


def weekday_label(weekday: int) -> str:
    if 0 <= int(weekday) <= 6:
        return WEEKDAYS_ZH[int(weekday)]
    return f"周{weekday}"
