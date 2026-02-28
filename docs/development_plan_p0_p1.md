# Nexus 开发方案（P0 + P1 落地版）

## 1. 目标范围
本次迭代聚焦两件事：

- P0（稳定性与可维护性）
  - 统一数据契约（Task / Archive JSON 输出结构）
  - 引入原子写，避免 `tasks.json` / `briefing.json` 写入损坏
  - 补充最小可执行测试，覆盖关键稳定性路径
- P1（Archive Sync 真闭环）
  - 从 Brightspace 作业页提取可下载附件（PDF/DOCX 等）
  - 下载到本地暂存目录，按规范重命名并归档
  - 在 stdout 输出严格 JSON 结果，stderr 仅日志

## 2. 设计原则

- stdout 只输出可解析 JSON（供 GUI 子进程消费）
- 所有调试信息写 stderr
- 写盘必须原子化（tmp + fsync + replace）
- 归档流程可重入、幂等（同一 URL/内容不会无限重复落盘）

## 3. P0 改造项

### 3.1 数据契约与校验
- 新增 `src/nexus/schemas.py`
  - `TASK_SCHEMA_VERSION`
  - `ARCHIVE_RESULT_SCHEMA_VERSION`
  - `validate_task_dict(payload)` 基础校验

### 3.2 原子写工具
- 新增 `src/nexus/io_utils.py`
  - `atomic_write_text(path, text)`
  - `atomic_write_json(path, data, ensure_ascii=False, indent=2)`
- 替换以下写入路径
  - `src/nexus/storage.py` 中 `save_projects/save_tasks/save_feeds`
  - `src/nexus/briefing_cli.py` 输出 `briefing.json`

### 3.3 最小测试
- 新增 `tests/test_io_utils.py`
- 新增 `tests/test_schemas.py`
- 新增 `tests/test_archive_utils.py`

## 4. P1 改造项

### 4.1 Archive Sync 下载闭环
- 在 `src/nexus/archive_sync/scraper.py` 新增：
  - 附件链接提取（从作业详情页提取可下载文件）
  - 归档命名（课程 + 标题 + 截止日期 + hash）
  - 文件下载（基于 Playwright context.request，复用登录态）
  - 产出 `archives` 列表（课程、原始文件名、归档路径、截止日期）

### 4.2 子进程输出契约
- 更新 `src/nexus/archive_sync/__main__.py`
  - stdout 返回结构包含：
    - `status`
    - `schema_version`
    - `tasks`
    - `data`（归档结果，兼容上层规范）
    - `message`
  - 继续将任务合并入 `tasks.json`

## 5. 验收标准

- `python -m nexus.briefing_cli --no-llm` 可正常写出 JSON
- `python -m nexus.archive_sync` 在缺失环境变量时返回规范错误 JSON
- Archive Sync 成功时 stdout 可解析，且包含 `data` 数组
- 关键 JSON 写入改为原子写
- 新增测试可运行通过（如本地安装 pytest）

## 6. 风险与回滚

- 风险：LMS 页面结构变化导致附件定位失败
  - 缓解：采用多选择器回退 + 失败记录到 `message`
- 风险：附件 URL 需要额外鉴权参数
  - 缓解：优先使用登录态请求，失败时记录 stderr 并跳过单文件
- 回滚策略：
  - 保留原任务抓取逻辑
  - 附件下载失败不影响 tasks 同步主链路

## 7. 后续（P2）

- 文档解析管线（Marker）
- Chroma 向量索引 + 元数据过滤
- 学习状态 `state.json`（JSON Schema + 原子写）
