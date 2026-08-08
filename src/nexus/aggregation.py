from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .aggregators.brightspace import BrightspaceAggregator, FeedConfig
from .aggregators.canvas import CanvasLMSConnector
from .aggregators.google import GoogleAggregator
from .config import AppConfig, default_config
from .failure_queue import failure_key, update_failure_queue
from .models import FeedSource, Task
from .storage import load_feeds, load_tasks, save_tasks
from .utils import dedupe_tasks


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
    warnings: list[str] = field(default_factory=list)
    source_status: dict[str, str] = field(default_factory=dict)
    failure_queue: dict[str, object] | None = None


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
    return dedupe_tasks(tasks)


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


def _google_configured(config: AppConfig) -> bool:
    return bool(
        config.google_credentials_path
        and config.google_token_path
        and config.google_credentials_path.exists()
        and config.google_token_path.exists()
    )


def _canvas_configured(config: AppConfig) -> bool:
    return bool(config.canvas_url and config.canvas_token)


def run_aggregation(
    include_google: bool = True,
    include_canvas: bool = True,
    data_dir: Path | None = None,
    config: AppConfig | None = None,
) -> AggregationResult:
    """
    执行完整的数据聚合流程。

    流程：
    1. 加载并分类订阅源。
    2. Brightspace (iCal/RSS) → 课程/作业数据。
    3. (可选) Google Calendar/Gmail。
    4. (可选) Canvas LMS REST API。
    5. 合并 + 去重 + 按来源合入旧快照（单个源失败不丢失该源历史数据）。
    6. 保存到磁盘 (tasks.json)。
    """
    config = config or default_config()
    data_dir = data_dir or config.data_dir
    tasks_path = data_dir / "tasks.json"
    feeds_path = data_dir / "feeds.json"
    tasks: list[Task] = []
    errors: list[str] = []
    warnings: list[str] = []
    source_status: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    resolved_failures: set[str] = set()
    existing_tasks = load_tasks(tasks_path)

    # Pre-compute per-source old tasks for snapshot preservation (Phase 4C-5)
    old_by_source: dict[str, list[Task]] = {}
    for t in existing_tasks:
        src = getattr(t, "source", None) or "unknown"
        old_by_source.setdefault(src, []).append(t)

    managed_sources = {"brightspace", "gcal", "gmail", "google", "canvas"}
    # User-owned, unknown/provider-extension, and browser-archive tasks are
    # outside feed/API snapshot replacement scope, so they must always survive.
    tasks.extend(
        task
        for source, source_tasks in old_by_source.items()
        for task in source_tasks
        if source == "manual"
        or source not in managed_sources
        or (source == "brightspace" and task.id.startswith("bspace_pw:"))
    )

    def preserve_snapshot(label: str, sources: tuple[str, ...], reason: str) -> None:
        old = [task for source in sources for task in old_by_source.get(source, [])]
        if old:
            warnings.append(f"{label} {reason}; preserving {len(old)} old tasks.")
            tasks.extend(old)

    feeds = load_feeds(feeds_path)
    ical_feeds, rss_feeds = _split_feeds(feeds)
    brightspace_enabled = bool(ical_feeds or rss_feeds)
    if not brightspace_enabled:
        warnings.append("Brightspace feeds not configured or disabled.")
        source_status["brightspace"] = "skipped"
        preserve_snapshot("Brightspace", ("brightspace",), "skipped")

    # 1. Brightspace
    if brightspace_enabled:
        try:
            tasks.extend(BrightspaceAggregator(ical_feeds, rss_feeds).fetch_tasks())
            source_status["brightspace"] = "succeeded"
            resolved_failures.add(failure_key({"operation": "aggregation", "source": "brightspace"}))
        except Exception as exc:
            errors.append(f"Brightspace error: {exc}")
            source_status["brightspace"] = "failed"
            failures.append({"operation": "aggregation", "source": "brightspace", "error": str(exc)})
            preserve_snapshot("Brightspace", ("brightspace",), "failed")

    # 2. Google
    if not include_google:
        warnings.append("Google aggregation disabled.")
        source_status["google"] = "disabled"
        preserve_snapshot("Google", ("gcal", "gmail", "google"), "disabled")
    elif not _google_configured(config):
        warnings.append("Google credentials or token not configured; source skipped.")
        source_status["google"] = "skipped"
        preserve_snapshot("Google", ("gcal", "gmail", "google"), "skipped")
    else:
        try:
            tasks.extend(GoogleAggregator(config=config).fetch_tasks())
            source_status["google"] = "succeeded"
            resolved_failures.add(failure_key({"operation": "aggregation", "source": "google"}))
        except Exception as exc:
            errors.append(f"Google error: {exc}")
            source_status["google"] = "failed"
            failures.append({"operation": "aggregation", "source": "google", "error": str(exc)})
            preserve_snapshot("Google", ("gcal", "gmail", "google"), "failed")

    # 3. Canvas (Phase 4C-6)
    if not include_canvas:
        warnings.append("Canvas aggregation disabled.")
        source_status["canvas"] = "disabled"
        preserve_snapshot("Canvas", ("canvas",), "disabled")
    elif not _canvas_configured(config):
        source_status["canvas"] = "skipped"
        preserve_snapshot("Canvas", ("canvas",), "skipped")
    else:
        try:
            connector = CanvasLMSConnector(config.canvas_url or "", config.canvas_token or "")
            canvas_tasks = connector.to_tasks()
            tasks.extend(canvas_tasks)
            source_status["canvas"] = f"succeeded ({len(canvas_tasks)} tasks)"
            resolved_failures.add(failure_key({"operation": "aggregation", "source": "canvas"}))
        except Exception as exc:
            errors.append(f"Canvas error: {exc}")
            source_status["canvas"] = "failed"
            failures.append({"operation": "aggregation", "source": "canvas", "error": str(exc)})
            preserve_snapshot("Canvas", ("canvas",), "failed")

    # 4. 去重 + 持久化
    tasks = _dedupe(tasks)
    if not tasks and existing_tasks:
        errors.append(
            "Aggregation produced no new tasks; preserving existing tasks.json."
        )
        save_tasks(existing_tasks, tasks_path)
        queue_report = _update_aggregation_failure_queue(
            data_dir, failures, resolved_failures, warnings
        )
        return AggregationResult(
            tasks=existing_tasks, errors=errors, warnings=warnings,
            source_status=source_status, failure_queue=queue_report,
        )

    save_tasks(tasks, tasks_path)
    queue_report = _update_aggregation_failure_queue(
        data_dir, failures, resolved_failures, warnings
    )
    return AggregationResult(
        tasks=tasks, errors=errors, warnings=warnings,
        source_status=source_status, failure_queue=queue_report,
    )


def _update_aggregation_failure_queue(
    data_dir: Path,
    failures: list[dict[str, str]],
    resolved: set[str],
    warnings: list[str],
) -> dict[str, object] | None:
    """Persist source failures without turning an observability fault into data loss."""
    try:
        return update_failure_queue(
            data_dir / "aggregation_failures.json",
            failures,
            resolved_attachment_urls=resolved,
        )
    except OSError as exc:
        warnings.append(f"Could not update aggregation failure queue: {exc}")
        return None


def sync_google_calendar(
    existing_tasks: list[Task] | None = None,
    data_dir: Path | None = None,
) -> AggregationResult:
    """
    轻量级同步：仅更新 Google Calendar 数据。
    
    场景：
    Google Calendar 的变动频率较高（如会议变更），且 API 响应较快。
    此函数允许在后台频繁运行，而无需每次都重新抓取所有数据源（特别是慢速的 Gmail 或 RSS）。
    """
    data_dir = data_dir or default_config().data_dir
    tasks_path = data_dir / "tasks.json"
    tasks = list(existing_tasks) if existing_tasks is not None else load_tasks(tasks_path)
    errors: list[str] = []
    warnings: list[str] = []
    config = default_config()
    if not _google_configured(config):
        warnings.append("Google credentials or token not configured; calendar sync skipped.")
        return AggregationResult(
            tasks=tasks,
            errors=errors,
            warnings=warnings,
            source_status={"google_calendar": "skipped"},
        )
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
    save_tasks(tasks, tasks_path)
    return AggregationResult(
        tasks=tasks,
        errors=errors,
        warnings=warnings,
        source_status={"google_calendar": "failed" if errors else "succeeded"},
    )


def check_brightspace_feeds(data_dir: Path | None = None) -> list[FeedStatus]:
    """
    诊断工具：逐个测试 Brightspace 订阅源的连通性。
    
    它不会保存数据，而是返回每个源的测试结果（成功/失败、获取到的条目数）。
    这对于调试 '为什么我的作业没显示' 非常有用。
    """
    data_dir = data_dir or default_config().data_dir
    feeds = load_feeds(data_dir / "feeds.json")
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
