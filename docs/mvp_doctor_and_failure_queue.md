# MVP: Aggregation Doctor + Archive Failure Queue

## 背景
MVP 阶段优先保证：
- 聚合前能快速发现环境/配置问题
- 归档下载失败可跨运行追踪，而不是一次性丢失

## 本次新增

### 1) Aggregation Doctor
- 新增 `src/nexus/doctor.py` 与 `src/nexus/doctor_cli.py`
- 检查项：
  - Python解释器路径
  - 核心依赖（requests/feedparser/icalendar）
  - Google依赖（可选）
  - token/credentials 文件存在性
  - feeds 配置是否存在启用项
  - tasks.json 是否存在

运行：
```bash
PYTHONPATH=src python -m nexus.doctor_cli
```

### 2) Archive Failure Queue
- 扩展 `src/nexus/archive_sync/reporting.py`
  - `load_failure_queue(...)`
  - `update_failure_queue(...)`
- 在 `archive_sync/__main__.py` 中：
  - 每次运行后更新 `data/archive_failures.json`
  - 新失败入队，已成功附件按 URL 自动出队
  - stdout 增加 `archive_failure_queue_count`

## 测试
- `tests/test_doctor.py`
- `tests/test_archive_failure_queue.py`
- `tests/test_archive_reporting.py`（新增队列读取覆盖）

## MVP价值
这两项改造减少了“盲跑”与“失败丢失”，使数据同步链路更易排障和重试，符合 MVP 的工程稳定性目标。
