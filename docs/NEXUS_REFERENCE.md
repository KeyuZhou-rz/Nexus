# Nexus 统一技术参考（单一文档）

## 1. 文档定位
本文件是 Nexus 当前唯一的主参考文档，覆盖：
- 项目组织方式（目录与分层）
- 关键接口（CLI + Python API）
- 核心方法实现与数据流
- 测试组织与运行方式

历史过程文档已归档到 `docs/archive/`，不作为日常开发入口。

## 2. 当前项目组织（个人可维护版）

### 2.1 顶层结构
- `src/nexus/`：业务代码
- `tests/`：自动化测试（`pytest`）
- `data/`：运行时数据（tasks/state/chroma 等）
- `docs/`：文档（本文件 + 归档）

### 2.2 `src/nexus` 分层
- `cli/`：CLI 实现和统一 `nexus <command>` 入口
- `archive_sync/`：Brightspace 抓取、附件归档、归档后处理
- `aggregators/`：Brightspace/Google 数据源聚合器
- `knowledge/`：知识入库与检索（extract/chunk/embed/store/query/read）
- `intelligence/`：briefing 与 LLM 调用
- 平级核心模块：`pipeline.py`、`aggregation.py`、`storage.py`、`models.py`、`streamlit_app.py`

## 3. 运行入口与接口

### 3.1 推荐主入口
```bash
nexus pipeline
```

### 3.2 常用 CLI
- Pipeline：`nexus pipeline [--skip-archive-sync] [--no-google] [--llm]`
- Briefing：`nexus briefing [--aggregate] [--window-days N] [--llm]`
- Archive Sync：`python -m nexus.archive_sync`
- Doctor：`nexus doctor [--no-google] [--strict]`
- Knowledge Ingest：`nexus ingest --input <path> --course-id <id> --doc-type <type>`
- Knowledge Query：`nexus query --query <text> [--course-id] [--doc-type]`
- Knowledge Read：`nexus read [--course-id] [--doc-type] [--source-path] [--limit]`
- Memory Extract：`nexus memory-extract --session-id <id>`
- Memory Feedback：`nexus memory-feedback --topic <topic> --action accept|reject`
- Memory Query：`nexus memory-query --query <text>`

## 4. 核心数据流（端到端）

### 4.1 Pipeline 流程
`run_mvp_pipeline(...)`（`src/nexus/pipeline.py`）按顺序执行：
1. `archive_sync`（可选）
2. `aggregation`
3. `briefing`

输出：
- `data/briefing.json`
- `data/pipeline_report.json`

### 4.2 Archive 流程
`python -m nexus.archive_sync`（`src/nexus/archive_sync/__main__.py`）执行：
1. Playwright 抓取任务与附件（`scraper.py`）
2. 合并任务到 `tasks.json`
3. 归档后处理 `run_archive_post_ingest(...)`
   - 文件抽取文本（txt/md/docx/pdf）
   - 统一入库到 Chroma
   - 提取弱项并写入 SQLite user profile
4. 更新失败队列 `data/archive_failures.json`

## 5. 关键方法与职责（Python API）

### 5.1 编排与聚合
- `pipeline.run_mvp_pipeline(...)`
  - 职责：统一流程编排与报告输出
- `aggregation.run_aggregation(include_google=True)`
  - 职责：聚合 Brightspace + Google，去重并写 `tasks.json`
- `archive_sync.run_archive_sync_subprocess(timeout=300)`
  - 职责：子进程执行 `nexus.archive_sync` 并返回 JSON

### 5.2 知识入库与检索
- `knowledge.chunking.chunk_text(text, max_chars=900, overlap=120)`
  - 职责：段落优先切分，超长文本滑窗切分
- `knowledge.document_text.extract_text(path)`
  - 职责：统一文档文本提取（`.md/.txt/.docx/.pdf`）；PDF 优先尝试 `updf4llm`，失败回退 `pypdf`
- `knowledge.ingestion_service.ingest_text_documents(documents, store, ...)`
  - 职责：统一 `chunk/id/metadata/upsert` 实现（当前唯一核心入库路径）
- `knowledge.ingest.ingest_files(paths, store, ...)`
  - 职责：读取支持格式文件后调用统一入库服务（当前 ingest 主接口）
- `knowledge.ingest.ingest_markdown_files(paths, store, ...)`
  - 职责：向后兼容接口，仅处理 `.md/.txt/.markdown`
- `archive_sync.post_ingest.run_archive_post_ingest(...)`
  - 职责：附件文本提取 + 调统一入库服务 + weak point 更新
- `knowledge.query.query_knowledge(db_dir, query_text, ...)`
  - 职责：按文本检索并支持 metadata 过滤
- `knowledge.read.read_chunks(db_dir, ...)`
  - 职责：按 metadata 过滤直接读取已入库 chunk，支持人工阅读/验收

### 5.3 记忆状态
- `memory_extractor.extract_weak_points(messages)`
  - 职责：从对话中规则提取薄弱点候选
- `memory_writer.record_weak_point(...)`
  - 职责：将弱项候选解析并写入 SQLite profile
- `knowledge.user_profile.UserProfileManager`
  - 职责：读取和更新用户弱项、学习 profile
- `knowledge.user_profile.UserProfileManager`
  - 职责：SQLite 中的 weak points 和学习 profile

### 5.4 简报
- `intelligence.briefing.build_briefing(tasks, ...)`
  - 职责：规则/LLM 生成 todo + schedule，并注入学习上下文
- `intelligence.briefing.briefing_payload(...)`
  - 职责：简报对象序列化为输出 JSON

## 6. 统一入库元数据规范（当前实现）
所有知识 chunk 统一字段：
- `file_name`
- `doc_type`
- `timestamp`
- `course_id`
- `chunk_index`
- `source_path`
- `session_id`

说明：
- `session_id` 在非对话场景可能为空字符串。
- 统一字段使查询过滤、调试、跨流程对接更稳定。

## 6.1 PDF 提取策略
- 优先：`updf4llm`（markdown 友好，适合后续分块和阅读）
- 回退：`pypdf`（基础文本提取）
- 如需启用优先路径：
```bash
pip install updf4llm
```

## 7. 运行时数据文件约定
- `data/tasks.json`：聚合后的任务总表
- `data/briefing.json`：简报输出
- `data/pipeline_report.json`：pipeline 步骤报告
- `data/nexus.db`：学习状态、概念掌握度和用户 profile
- `data/archive_failures.json`：归档失败队列
- `data/chroma/`：Chroma 持久化目录

## 8. 测试组织与使用

### 8.1 结构
- `tests/test_*.py` 扁平组织，按功能域命名：
  - `test_archive_*`
  - `test_aggregation_*`
  - `test_knowledge_*`
  - `test_memory_*`
  - `test_pipeline.py` / `test_doctor.py`

### 8.2 运行
```bash
PYTHONPATH=src pytest -q
```

## 9. 当前维护原则（精简版）
- 单一主入口：`nexus pipeline`
- 单一核心入库实现：`knowledge/ingestion_service.py`
- CLI 逻辑集中在 `nexus/cli/`
- 兼容入口仅做转发，不堆业务逻辑
- 新增功能优先放入现有分层，不新增“平级杂项模块”

## 10. 后续重构优先级
1. 拆分 `streamlit_app.py`（当前最大复杂度来源）
2. 为核心数据契约补 schema 级测试（pipeline_report / state / archive result）
3. 引入 `ruff + mypy` 作为轻量质量闸门
