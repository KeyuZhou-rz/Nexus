from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .aggregators.brightspace import BrightspaceAggregator, FeedConfig
from .aggregators.google import GoogleAggregator
from .models import FeedSource, Task
from .storage import load_feeds, load_tasks, save_tasks


@dataclass
class AggregationResult:
    """
    聚合运行的结果容器。
    
    Attributes:
        tasks: 聚合且去重后的所有任务列表。
        errors: 运行过程中捕获的异常信息列表（如网络超时、API 错误）。
    """
    tasks: list[Task]
    errors: list[str]


@dataclass
class FeedStatus:
    """
    单个订阅源的健康状态诊断信息。
    用于在 UI 的 'Feed Diagnostics' 面板中显示每个源是否正常工作。
    """
    name: str
    kind: str
    url: str
    enabled: bool
    ok: bool
    item_count: int
    error: str | None = None


def _dedupe(tasks: Iterable[Task]) -> list[Task]:
    """
    任务去重逻辑。
    
    原理：
    1. 优先使用任务自带的唯一 ID (如 Google Calendar 的 event ID)。
    2. 如果没有 ID，则根据 '来源:标题:截止时间' 生成一个复合键作为指纹。
    3. 保留每个指纹第一次出现的任务，丢弃后续重复项。
    """
    seen: dict[str, Task] = {}
    for task in tasks:
        key = task.id or f"{task.source}:{task.title}:{task.due_at}"
        if key not in seen:
            seen[key] = task
    return list(seen.values())


def _split_feeds(feeds: list[FeedSource]) -> tuple[list[FeedConfig], list[FeedConfig]]:
    """
    将存储层读取的 FeedSource 配置转换为聚合器需要的 FeedConfig 对象，
    并按类型（iCal vs RSS）进行分类。
    
    Returns:
        (ical_feeds, rss_feeds): 两个列表的元组。
    """
    ical_feeds: list[FeedConfig] = []
    rss_feeds: list[FeedConfig] = []
    for feed in feeds:
        if not feed.enabled:
            continue
        
        # 将通用的 FeedSource 转换为 Aggregator 专用的 FeedConfig
        config = FeedConfig(
            name=feed.name,
            url=feed.url,
            course=feed.course,
            audience=feed.audience,
            mode=feed.mode,
        )
        
        if feed.kind in {"brightspace_ical", "ical_file"}:
            ical_feeds.append(config)
        elif feed.kind == "brightspace_rss":
            rss_feeds.append(config)
    return ical_feeds, rss_feeds


def run_aggregation(include_google: bool = True) -> AggregationResult:
    """
    执行完整的数据聚合流程。
    
    流程：
    1. 加载并分类订阅源。
    2. 调用 BrightspaceAggregator 获取课程/作业数据。
    3. (可选) 调用 GoogleAggregator 获取邮件/日历数据。
    4. 合并所有数据并去重。
    5. 保存到磁盘 (tasks.json)。
    """
    tasks: list[Task] = []
    errors: list[str] = []

    feeds = load_feeds()
    ical_feeds, rss_feeds = _split_feeds(feeds)
    
    # 1. 处理 Brightspace 数据源
    if ical_feeds or rss_feeds:
        try:
            tasks.extend(BrightspaceAggregator(ical_feeds, rss_feeds).fetch_tasks())
        except Exception as exc:
            errors.append(f"Brightspace error: {exc}")

    # 2. 处理 Google 数据源
    if include_google:
        try:
            tasks.extend(GoogleAggregator().fetch_tasks())
        except Exception as exc:
            errors.append(f"Google error: {exc}")

    # 3. 数据清洗与持久化
    tasks = _dedupe(tasks)
    save_tasks(tasks)
    return AggregationResult(tasks=tasks, errors=errors)


def sync_google_calendar(existing_tasks: list[Task] | None = None) -> AggregationResult:
    """
    轻量级同步：仅更新 Google Calendar 数据。
    
    场景：
    Google Calendar 的变动频率较高（如会议变更），且 API 响应较快。
    此函数允许在后台频繁运行，而无需每次都重新抓取所有数据源（特别是慢速的 Gmail 或 RSS）。
    """
    tasks = list(existing_tasks) if existing_tasks is not None else load_tasks()
    errors: list[str] = []
    try:
        # 仅请求 Calendar 数据，不请求 Gmail
        calendar_tasks = GoogleAggregator(include_gmail=False, include_calendar=True).fetch_tasks()
        
        # 移除旧的 gcal 数据，保留其他来源的数据（如 brightspace, gmail）
        tasks = [task for task in tasks if task.source != "gcal"]
        # 追加新的 gcal 数据
        tasks.extend(calendar_tasks)
    except Exception as exc:
        errors.append(f"Google Calendar sync error: {exc}")
        
    tasks = _dedupe(tasks)
    save_tasks(tasks)
    return AggregationResult(tasks=tasks, errors=errors)


def check_brightspace_feeds() -> list[FeedStatus]:
    """
    诊断工具：逐个测试 Brightspace 订阅源的连通性。
    
    它不会保存数据，而是返回每个源的测试结果（成功/失败、获取到的条目数）。
    这对于调试 '为什么我的作业没显示' 非常有用。
    """
    feeds = load_feeds()
    statuses: list[FeedStatus] = []
    for feed in feeds:
        if feed.kind not in {"brightspace_ical", "brightspace_rss", "ical_file"}:
            continue
            
        if not feed.enabled:
            statuses.append(
                FeedStatus(
                    name=feed.course or feed.name,
                    kind=feed.kind,
                    url=feed.url,
                    enabled=False,
                    ok=False,
                    item_count=0,
                    error="disabled",
                )
            )
            continue
        try:
            # 隔离测试：为每个 feed 创建一个临时的 Aggregator 实例
            if feed.kind == "brightspace_rss":
                tasks = BrightspaceAggregator(
                    [],
                    [
                        FeedConfig(
                            feed.name,
                            feed.url,
                            course=feed.course,
                            audience=feed.audience,
                            mode=feed.mode,
                        )
                    ],
                ).fetch_tasks()
            else:
                tasks = BrightspaceAggregator(
                    [
                        FeedConfig(
                            feed.name,
                            feed.url,
                            course=feed.course,
                            audience=feed.audience,
                            mode=feed.mode,
                        )
                    ],
                    [],
                ).fetch_tasks()
            statuses.append(
                FeedStatus(
                    name=feed.course or feed.name,
                    kind=feed.kind,
                    url=feed.url,
                    enabled=True,
                    ok=True,
                    item_count=len(tasks),
                )
            )
        except Exception as exc:
            statuses.append(
                FeedStatus(
                    name=feed.course or feed.name,
                    kind=feed.kind,
                    url=feed.url,
                    enabled=True,
                    ok=False,
                    item_count=0,
                    error=str(exc),
                )
            )
    return statuses
