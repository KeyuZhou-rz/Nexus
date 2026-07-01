"""跨模块共享的内部工具函数。

收录此前在多个模块中重复定义的小工具（ID 生成、任务去重、时区本地化、主题归一化），
统一实现以避免行为漂移。仅限 nexus 内部使用。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Iterable

from .models import Task

# 主题归一化用正则：保留 CJK、字母、数字、连字符、空白，其余替换为空格。
TOPIC_CLEAN = re.compile(r"[^一-鿿A-Za-z0-9\-\s]")


def stable_id(seed: str) -> str:
    """从字符串种子生成稳定的短哈希 ID（sha256 前 16 位）。"""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def dedupe_tasks(tasks: Iterable[Task]) -> list[Task]:
    """
    任务去重：优先用任务自带 id，否则用 '来源:标题:截止时间' 作指纹，
    保留每个指纹首次出现的任务。
    """
    seen: dict[str, Task] = {}
    for task in tasks:
        key = task.id or f"{task.source}:{task.title}:{task.due_at}"
        if key not in seen:
            seen[key] = task
    return list(seen.values())


def localize_dt(value: datetime | None, now_local: datetime) -> datetime | None:
    """将 datetime 本地化到 now_local 的时区；naive datetime 视为该时区。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(now_local.tzinfo)
    return value.replace(tzinfo=now_local.tzinfo)


def normalize_topic(raw: str) -> str:
    """主题归一化：剥离标点、转小写、合并多余空白。"""
    text = TOPIC_CLEAN.sub(" ", raw or "").strip().lower()
    return re.sub(r"\s+", " ", text)
