# MVP 聚合故障诊断与修复

## 诊断结论
- 聚合链路中 Google 依赖缺失会触发 `Google error`。
- `data/feeds.json` 缺失/未配置时 Brightspace 无数据源。
- 在上述场景下，聚合可能写回空任务列表，导致 `tasks.json` 被清空。

## 本次修复
- `src/nexus/aggregation.py`
  - 增加 Brightspace 源未配置提示：`Brightspace feeds not configured or disabled.`
  - 增加失败回退保护：
    - 当抓取结果为空且来源报错时，保留并写回已有 `tasks.json` 快照
    - 附加错误信息：`preserving existing tasks.json`
- `src/nexus/archive_sync/scraper.py`
  - 增强课程发现流程（多 URL 回退 + 多 selector 语义匹配），降低 `0 courses found` 误判概率。
  - 新增课程发现诊断输出：`course_discovery`（尝试 URL、使用 selector、命中数量）。
  - 新增调试工件落盘：当课程发现失败时自动保存页面 HTML/截图/元数据到 `tmp/debug/archive_sync/`。
  - 修复 OU 提取正则与标题清洗正则，确保 `/d2l/home/{ou}` 与 `| Brightspace` 后缀可正确解析。
- `src/nexus/archive_sync/__main__.py`
  - 新增环境变量：
    - `NEXUS_ARCHIVE_DEBUG`（默认开启）
    - `NEXUS_ARCHIVE_DEBUG_DIR`（默认 `tmp/debug/archive_sync`）
    - `NEXUS_ARCHIVE_COURSE_DISCOVERY_TIMEOUT`（默认 `25000ms`）
  - 将上述参数透传给抓取器，便于线上快速诊断。

## 测试覆盖
- `tests/test_aggregation_resilience.py`
  - 源失败时保留旧任务
  - 无 feed 配置时返回明确错误
- `tests/test_archive_course_discovery.py`
  - 课程 URL 回退列表生成
  - `ou` 参数解析（query/path 两种形式）
  - 课程标题清洗
  - 课程链接去重与噪声过滤

## MVP意义
这次修复优先保证“数据不丢失 + 可诊断性”，避免操作失败导致任务数据被覆盖为空，并在 Brightspace 页面结构变化时快速定位问题，满足 MVP 对稳定性的基本要求。
