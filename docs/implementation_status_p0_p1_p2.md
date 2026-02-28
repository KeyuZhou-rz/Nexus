# Nexus 实施状态（P0 / P1 / P2）

## 更新时间
- 2026-02-28

## P0（稳定性与可维护性）
### 已完成
- 引入原子写工具，覆盖 `tasks/projects/feeds/briefing` 核心 JSON 输出。
- 增加任务基础 schema 校验，避免脏数据写入。
- 新增测试：
  - `test_io_utils.py`
  - `test_schemas.py`
  - `test_aggregation_smoke.py`

### 待完成
- Streamlit 超大文件的进一步模块拆分（当前仍为单文件主结构）。

## P1（Archive Sync 真闭环）
### 已完成
- 作业详情页附件提取并下载归档。
- 归档命名标准化（课程/标题/日期/hash）。
- 子进程 stdout JSON 增加 `schema_version` / `data` / `archive_failures`。
- 下载重试机制（网络抖动容错）。
- 失败明细持久化到 `data/archive_failures.json`。
- Streamlit 增加归档明细与失败明细展示。
- 新增测试：
  - `test_archive_utils.py`
  - `test_archive_reporting.py`
  - `test_archive_main_contract.py`

### 待完成
- 基于真实 Brightspace 页面进一步调优附件选择器（不同课程模板差异）。
- 增加失败重试队列（跨进程/跨运行恢复）。

## P2（解析与记忆层）
### 尚未开始（本轮仅做准备）
- 文档摄入与解析管线（Marker/Chunk/Embedding）
- Chroma 持久化与 metadata filtering
- `state.json` 学习状态模型 + briefing 联动

## 下一迭代建议
1. 完成 Brightspace 真实页面联调并固化选择器样本。
2. 开启 P2 最小链路：本地文档 -> chunk -> embedding -> Chroma 查询。
3. 在 UI 增加“知识检索/学习状态”调试面板。
