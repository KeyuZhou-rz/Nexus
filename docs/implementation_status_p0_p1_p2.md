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
### 已完成（最小链路）
- 新增知识摄入子包 `nexus.knowledge`：
  - `chunking.py`：文本切块
  - `embedding.py`：本地确定性 hash embedding
  - `store.py`：Chroma 持久化封装
  - `ingest.py`：批量摄入与 metadata 写入
- 新增 `ingest_cli.py`，可从目录/文件摄入 `.md/.txt`。
- 新增 `state_store.py`（`LearnerState` + 原子写读）。
- 新增测试：
  - `test_knowledge_chunking.py`
  - `test_knowledge_embedding.py`
  - `test_knowledge_ingest.py`
  - `test_state_store.py`

### 待完成
- 对接 Marker/PDF 解析进入 ingest。
- 接入真实语义 embedding（可切换本地/云模型）。
- 在 Streamlit 中增加知识检索与学习状态调试面板。
- 将 state 与 briefing 做联动生成。

## 下一迭代建议
1. 打通“PDF -> markdown -> ingest”链路。
2. 加入 query CLI 和 metadata filtering 验证用例。
3. Streamlit 增加 P2 面板并展示 state/chroma 检索结果。
