# Nexus 开发路线图 (ROADMAP)

> 自动化推进积压清单。`/nexus-dev` 每次取最上方未完成项推进。
> 状态标记：`[ ]` 待办 · `[~]` 进行中 · `[x]` 完成 · `[!]` 阻塞
> 优先级：Phase T（测试地基）> Phase M（记忆地基）> Phase L（LLM 网关）> Phase B（简报）> Phase U（辅导）> Phase E（邮件）> Phase X（LMS 抽象）
>
> **愿景**：可接入大模型 API + 接管邮件重要信息 + Canvas 内容 + 结构化记忆 + 私人定制化学习辅导。
> **依赖链**：T → M → L → B → U → E → X（M 是 U/B 地基；L 是 E/U 的 LLM 依赖；X 独立）。

---

## Phase T — 测试地基（最高优先）

先把 6 个红测试修绿、补齐薄测试，为新功能开发建立安全网。

- [x] **T1 修复 chunk_text 签名断裂** [critical]
  - 现象：`chunking.py` 的 `chunk_text(text)` 仅按 `\n\n` 切分，丢失 `max_chars`/`overlap` 参数；`ingestion_service.py:67` 仍按 `chunk_text(content, max_chars=900, overlap=120)` 调用 → 6 测试全红。
  - 修复：恢复字符滑窗实现 —— 按 `max_chars` 切窗、`overlap` 重叠；`overlap >= max_chars` 抛 `ValueError`（测试 `test_knowledge_chunking.py` 已锁定该契约）。
  - 验收：`make test` 全绿；`test_knowledge_chunking.py` 两条通过。
  - 文件：`src/nexus/knowledge/chunking.py`（只读参考 `tests/test_knowledge_chunking.py`）

- [x] **T2 补强 memory_extractor 测试**
  - 现状：`tests/test_memory_extractor.py` 仅 2 条（中英模式各一）。
  - 补：空消息、无弱项信号、多条混合、证据 msg_id 透传、置信度边界。
  - 文件：`tests/test_memory_extractor.py`、`src/nexus/memory_extractor.py`

- [x] **T3 补强 memory_update 测试**
  - 现状：`tests/test_memory_update.py` 3 条。
  - 补：合并去重、置信度下限 clamp、reject 后 review_queue 清理、decay 跨日边界。
  - 文件：`tests/test_memory_update.py`、`src/nexus/memory_update.py`

- [x] **T4 briefing 单元覆盖**
  - 现状：仅 `test_briefing_context.py`。
  - 补：`select_exam_reminders` 窗口边界、`_is_noise` 过滤、`_rule_briefing` 降级路径、`_normalize_title`/`_contains_cjk`。
  - 文件：`tests/test_briefing_unit.py`（新建）、`src/nexus/intelligence/briefing.py`

- [x] **T5 dashboard view_model 错误路径**
  - 现状：`tests/test_dashboard_view_model.py` 85 行，覆盖正向。
  - 补：空任务、缺失字段、时区边界。
  - 文件：`tests/test_dashboard_view_model.py`、`src/nexus/dashboard/view_model.py`

---

## Phase M — 记忆地基（U/B 的地基）

**决策（已冻结）**：分层不合并 —— `knowledge/`(SQLite, Ebbinghaus, 概念图谱) 为主存储；根级 `memory_*` 降级为别名词典 + 零成本弱项兜底；`state.json` 不再生成（已无数据，迁移风险≈0）。concept ID 对齐走 `concept_resolver` 三级降级（别名表→规范化→模糊匹配≥0.7）。图谱填充从 Chroma 70 chunks 回填 SQLite `chunks` 表 + 跑 `concept_extractor`，不重跑 LLM 解析。

- [x] **M1-1 设计冻结**（design，无代码）：分层、主存储和 concept ID 对齐决策已写入本节。
- [x] **M1-2 SQLite chunks 回填**：新建 `knowledge/chunk_backfill.py` + `cli/concepts_backfill.py`，从 Chroma 回填 SQLite `chunks` 表 + 补 `documents`。验收：`SELECT COUNT(*) FROM chunks` ≥ 47。测试 `tests/test_chunk_backfill.py`。
- [x] **M1-3 别名表 + concept_resolver**：`schema.sql` 加 `concept_aliases`(alias/course_id/concept_id/source, UNIQUE(alias,course_id))；新建 `concept_resolver.py` 三级 `resolve`；`sqlite_store` 加 `upsert_alias`/`get_alias`。测试 `tests/test_concept_resolver.py`。
- [x] **M1-4 跑通 concept_extractor 填充图谱**（核心）：扩展回填 CLI，回填后调 `ConceptExtractor.extract_from_chunks → refine_with_llm(有 key) → persist`。验收：`concepts`>0、`chunk_concepts`>0、`GraphRetriever.retrieve([chunk_id])` 返 `has_content=True`。测试 `tests/test_concept_backfill.py`（stub LLM）。
- [x] **M1-5 统一写口 memory_writer**：新建 `memory_writer.py`，`record_weak_point`(resolve→add_weak_point，命中记 struggled) + `apply_feedback_unified`(reject→remove+forgot，accept→mastered+correct_answer)。测试 `tests/test_memory_writer.py`。
- [x] **M1-6 三个根级入口改走 memory_writer**：改 `cli/memory_extract.py`、`cli/memory_feedback.py`、`archive_sync/post_ingest.py`，移除 state.json 读写。验收：生产代码不再包含 `state_store.py`/`state.json` 路径；`test_archive_post_ingest` 改断言到 SQLite user_profile。
- [x] **M1-7 UnifiedMemoryReader + briefing 改造**：新建 `knowledge/unified_memory.py`(`weak_points`/`review_queue`=get_decaying_concepts/`low_mastery_topics`)；`briefing._inject_learning_context` 用 reader 替代 `load_state`。验收：`grep load_state briefing.py` 无残留；`test_briefing_context` 改 monkeypatch reader。B1 前置。
- [x] **M1-8 弃用标记 + 文档**：`state_store.py`/`memory_update.py` 已移除；当前记忆入口为 `conversation_store.py` / `memory_writer.py` / `knowledge/temporal_memory.py`；ROADMAP M1 标 `[x]`。验收：96 绿。

**风险**：M1-2/3/4 纯新增不碰现有测试；M1-6/7 高风险（同步改 `test_archive_post_ingest`/`test_briefing_context` 断言，语义保留）；数据改动前 `cp data/nexus.db data/nexus.db.bak`。

---

## Phase B — 简报系统（M1-7 后扩展）

- [x] **B1 briefing 注入 temporal 掌握度**
  - `_inject_learning_context` 显式接入 `temporal_memory.get_decaying_concepts`（"今日该复习什么"）。M1-7 已切读 UnifiedMemoryReader，B1 在此基础上注入 decaying 概念。
  - 交付：显式 `TemporalMemory` 实例化 + `get_decaying_concepts()` 调用；按 urgency 排序 + mastery% 注入 review item；弱项兜底保留。
  - 文件：`src/nexus/intelligence/briefing.py`、`src/nexus/knowledge/temporal_memory.py`

- [x] **B2 LLM 简报失败降级测试**
  - `_llm_briefing` 异常/超时 → `_rule_briefing` 兜底，确保不抛。
  - 交付：6 tests（LLMError/通用异常降级、未配置降级、use_llm=False 跳过、warm_message/focus 保留、空 tasks 不崩）。
  - 文件：`tests/test_briefing_fallback.py`（新建）

- [x] **B3 考试提醒窗口边界**
  - `select_exam_reminders(window_days=180)` 边界、跨年、无考试。
  - 交付：8 new tests（窗口边界 inclusive/exclusive、今日考试、空列表、自定义窗口 30d、跨年 365d、TZ 跨年、TZ 边界）。
  - 文件：`tests/test_briefing_unit.py`

---

## Phase L — LLM 统一网关（M 后）

- [x] **L1 抽 `knowledge/llm_gateway.py` 单例**：收敛 7 处硬编码 Qwen（`query_engine`/`session_manager`/`concept_extractor`×2/`parser` + `intelligence/llm.py`），统一读 `NEXUS_LLM_MODEL`/`NEXUS_LLM_API_KEY`/`NEXUS_LLM_BASE_URL`，Qwen 默认可换 OpenAI/Claude。
- [x] **L2 key 管理统一**：合并 `QWEN_API_KEY.json` 与 `NEXUS_LLM_API_KEY`。
- [x] **L3 交互日志统一**：`log_interaction` 覆盖简报侧。

## Phase U — 辅导深度（依赖 M+L）

- [x] **U1 苏格拉底引导**：改写 `prompts.QA_SYSTEM_PROMPT`，基于 mastery 低位/weak_point 命中触发分步追问；`context_assembler` 按 mastery 选引导策略。
  - 交付：`SOCRATIC_GUIDANCE` 5 级策略（critical/decaying/stable/mastered/unknown）；`_pick_socratic_level()` 按最低 mastery 选级；`assemble_messages()` 接受 `mastery_map`；`qa_pipeline.ask()` 在 Step 3.6 查询 mastery 注入。
  - 文件：`prompts.py`, `context_assembler.py`, `qa_pipeline.py`, `tests/test_context_assembler.py`（22 tests）

- [x] **U2 学习计划生成**：新建 `knowledge/study_planner.py`，LLM 结合考试日(Calendar) + 掌握度(temporal_memory) + 前置关系(graph_retriever 拓扑序) 生成周计划。
  - 交付：`StudyPlanner` 类（LLM 增强 + 规则兜底）；`_compute_prereq_depths()` BFS 拓扑深度；7 天每日计划（focus/rationale/estimated_minutes）；无 LLM 时 rule-based priority sort。
  - 文件：`knowledge/study_planner.py`, `tests/test_study_planner.py`（10 tests）

- [x] **U3 掌握度可视化**：Streamlit 加遗忘曲线图 + 概念图谱 + 学习计划视图（消费 `get_mastery_overview`/`get_decaying_concepts`）。
  - 交付：新 tab "📊 Learning Progress"；Metrics（total/critical/decaying/mastered）；Mastery Distribution bar chart；Concept table；Ebbinghaus 遗忘曲线（30天模拟）；Quick Study Plan（StudyPlanner 集成）。
  - 文件：`streamlit_app.py`

## Phase E — 邮件重要信息（依赖 L）

- [x] **E1 Gmail 全文拉取**：`format="full"` + MIME 解析拿正文。
  - 交付：`_decode_email_body()` 递归 MIME 解码（text/plain, text/html, multipart/alternative, multipart/mixed）；`_strip_html()` 标签剥离+实体解码；`format="full"` 替代 `format="metadata"`。
  - 文件：`aggregators/google.py`

- [x] **E2 LLM 重要性/结构化提取**：考试时间/截止/行动项 → 结构化字段，非 snippet 截断。
  - 交付：`_extract_structured_fields()` LLM 提取（exam_date/deadline/action_items/course_hint/priority/summary_zh）；`_enrich_task_from_llm()` 应用提取结果到 Task（不覆盖已有 course）；LLM 不可用时优雅降级。
  - 文件：`aggregators/google.py`

- [x] **E3 课程归属修复 + 分页**：`course_aliases.json` 缺失问题 + 时间窗口/分页拉取。
  - 交付：pageToken 分页循环；`gmail_window_days` 时间窗口过滤（默认 30 天）；`gmail_max_results` 可配置上限（默认 20）；`_load_course_aliases` 文件缺失/JSON 非法/非数组均返回空列表。
  - 文件：`aggregators/google.py`, `tests/test_gmail_enhanced.py`（27 tests）

## Phase X — LMS 抽象（独立，最后）

- [x] **X1 抽 `aggregators/lms_base.py` 接口**：courses/assignments/announcements/files。
  - 交付：`LMSConnector(ABC)` + 4 抽象方法；`Course`/`Assignment`/`Announcement`/`CourseFile` 数据类；`to_tasks()` 统一转换。
  - 文件：`aggregators/lms_base.py`

- [x] **X2 Brightspace 适配为实现**：archive_sync + iCal/RSS 路径。
  - 交付：`BrightspaceLMSConnector(LMSConnector)` — iCal → assignments, RSS → announcements, archive_dir → files。
  - 文件：`aggregators/brightspace_lms.py`

- [x] **X3 新建 `aggregators/canvas.py`**：Canvas REST API + OAuth token。
  - 交付：`CanvasLMSConnector(LMSConnector)` — REST API + Link header 分页 + Bearer token 认证。
  - 文件：`aggregators/canvas.py`

---

---

## Phase 4 — 生产可用性与 Progress 精度收口

> 当前发布基线：schema v9、718 passed（正式 `tests/` 套件）、DB consistency 0 issues。
> 不做前端视觉设计。每个子项单独提交；每完成一个阶段跑定向测试、全量测试、CLI smoke 和 db-check。
> 数据库、Chroma、凭据和 pycache 不进入提交。

### 4A：P0 安装与启动闭环

目标：新环境按文档安装后，可以可靠启动和诊断。

- [x] **4A-1 重构依赖为 base + optional extras**
  - `pyproject.toml` 基础依赖提供 dashboard/离线核心；`google`、`archive`、`pdf`、`llm` 为可选能力，`all` 聚合完整安装。
  - 消灭 requirements.txt（pyproject.toml extras 替代）
  - 验收：`pip install -e .` 可运行离线 QA、数据库命令和 pipeline
  - (done: pyproject.toml — google/archive/pdf/llm/all extras 已定义；requirements.txt 已不存在)

- [x] **4A-2 CI 增加真实 pip install + 矩阵构建**
  - Python 3.10-3.13 矩阵
  - `pip install -e .[core,google,archive,pdf,llm]` + `pip check`
  - CLI smoke: doctor/db-check/query --help + 无 import crash
  - 验收：矩阵全绿
  - (done: .github/workflows/tests.yml — Python 3.10-3.13, `.[all]`, pip check, CLI smoke)

- [x] **4A-3 统一 Doctor 的 enabled/configured/available 语义**
  - 当前 Doctor 只有二元 ok/failed；补充三级诊断：
    - `enabled`: 用户是否启用该功能（env/config）
    - `configured`: 凭据/路径是否就绪（文件存在、权限正确）
    - `available`: 运行时依赖是否可用（import 成功、API 连通）
  - 扩展检查：ChromaDB embedding model、Python 版本、WAL 模式确认、Playwright browser binary、LLM API 连通性
  - 验收：`nexus doctor` 输出三级状态；缺少可选依赖时明确诊断，不产生 import crash
  - (done: src/nexus/doctor.py — ComponentStatus 实现 enabled/configured/available 三级诊断)

- [x] **4A-4 补齐 .env.example 和首次启动检查**
  - `.env.example` 已覆盖 LLM、Google、Brightspace、Canvas、Archive、PDF 和运行目录配置。
  - `nexus doctor` 对首次启动时缺失的 SQLite/Chroma 存储给出可操作诊断；clean-room 冒烟测试已锁定离线启动契约。

- [x] **4A-5 scheduler 示例**
  - pipeline: 每日 cron/systemd timer 示例
  - session finalize: 定期清理过期 session
  - backup: 每周备份 cron 示例
  - 验收：示例可复制粘贴使用
  - (done: deploy/ — cron, systemd, launchd pipeline/session/backup examples)

### 4B：P0 索引与历史数据治理

目标：把当前可用但仍带旧契约的数据升级到统一索引格式。

- [x] **4B-1 只读索引审计**
  - 扩展 `KnowledgeConsistencyAudit`：metadata、collection/course、document、分页完整性
  - 输出精确的待修复数量和分布
  - (done: `nexus db-check` + KnowledgeConsistencyAudit)

- [x] **4B-2 metadata/hash 迁移**
  - 为旧向量补齐 `schema_version`/`document_id`/`chunk_id`/`file_hash`/`chunk_index`
  - 将旧 MD5 document hash 迁移到 SHA-256
  - SQLite chunks 表加 `chunk_index` 列（如缺失）
  - (done: MetadataMigration dry-run/apply + migration tests)

- [x] **4B-3 concept quarantine/cleanup**
  - 识别并隔离无 chunk 证据的孤立 concept
  - 清理 Berkshire/投资主题及"如何、区别"等伪概念
  - 默认 dry-run；确认后执行
  - (done: `nexus db-repair --quarantine-concepts`, reversible quarantine table)

- [x] **4B-4 Chroma 版本兼容**
  - 固定经过验证的 Chroma 版本范围
  - 检查内部 foreign key 一致性
  - (done: pyproject.toml — `chromadb>=1.0`)

- [x] **4B-5 迁移安全保障**
  - 每次 apply 前自动备份
  - 迁移后执行 db-check 和检索 golden set
  - (done: SafeMigration is now required for db-repair writes; pre-backup failure blocks apply; post-audit covered by `tests/test_safe_migrations.py`)

### 4C：P1 入库路径统一

目标：所有入口写入同一双写管线，避免"导入成功但 Progress 不可见"。

- [x] **4C-1 legacy Markdown/Text ingest 接入双写**
  - 将 `ingestion_service.ingest_text_documents` 路径接入 SQLite documents/chunks 表
  - 验收：.md/.txt 导入后能被 query、assessment、progress 消费
  - (done: src/nexus/knowledge/ingestion_service.py — 传入 sqlite_store 时执行 SQLite dual-write)

- [x] **4C-2 archive post-ingest 接入 CourseIngestor**
  - `post_ingest.py` 统一走 `CourseIngestor` 双写管线
  - 验收：archive 导入的文件在 SQLite 中可见
  - (done: src/nexus/archive_sync/post_ingest.py 导入 CourseIngestor)

- [x] **4C-3 跨进程并发保护**
  - 为同一 document 增加 lease/generation token
  - 两个进程并发导入同一文件不互删、不留半成品
  - (done: per-course/document exclusive lease; `tests/test_ingest_document_lock.py` covers same-document serialization and unrelated-document concurrency)

- [x] **4C-4 批量写入 SQLite**
  - 避免逐 chunk 事务，改为批量 commit
  - (done: src/nexus/knowledge/sqlite_store.py — `upsert_chunks`/`_bulk_insert_chunks` 批量写入)

- [x] **4C-5 aggregation source-level snapshot**
  - 每个来源独立 snapshot，单个来源失败时保留该来源旧数据
  - 验收：任一外部源失败不清除其他来源或该来源的上次快照
  - (done: src/nexus/aggregation.py — Phase 4C-5 预计算 per-source old tasks)

- [x] **4C-6 Canvas 接入 aggregation 和 Doctor**
  - Canvas connector 接入 `run_aggregation` 主流程
  - Doctor 检查 Canvas 凭据和连通性
  - (done: aggregation.py Canvas source and doctor Canvas component)

### 4D：P1 Progress 后端收敛

目标：减少低价值模块，让 Progress 输出更准确、可解释。

- [x] **4D-1 建立单一 ProgressService**
  - 统一读取 mastery、assessment、review queue
  - 替代分散的 TemporalMemory/MemoryAPI/UserProfileManager 直接调用
  - (done: src/nexus/knowledge/progress.py — ProgressService 类已存在)

- [x] **4D-2 模块分级**
  - 核心：Mastery、Assessment、Review Queue
  - 条件启用：Study/Sprint Plan
  - Experimental：Gap Scanner、Retrospective
  - Experimental 模块不影响核心 mastery
  - (done: ProgressService MODULE_TIERS)

- [x] **4D-3 无可信 assessment 数据时不输出伪精确 mastery**
  - 检查 `learning_events.trust_status` 过滤
  - 普通 QA、被动阅读和模型推断不修改 mastery
  - (done: TemporalMemory trusted evidence gate)

- [x] **4D-4 Sprint 时间预算**
  - 根据概念数量、难度和可用时间计算时间预算
  - 非简单线性分配
  - (done: SprintPlanner._estimate_minutes uses concept count, mastery gap, and daily cap)

- [x] **4D-5 Gap Scanner 批量统计**
  - 消除 N+1 查询
  - (done: SQLite batch concept query stats; GapScanner consumes one aggregated query)

- [x] **4D-6 Retrospective 命中率**
  - 只比较考前持久化快照与用户确认结果
  - 不允许 LLM 自由生成后自我评分
  - (done: immutable future-exam mastery snapshots; retrospective rejects missing/invalid historical snapshots and filters LLM hits to stored predictions)

- [x] **4D-7 离线校准集**
  - 建立显式 assessment 事件与置信度的离线校准数据契约。
  - `nexus calibration --fixture ...` 输出 accuracy、ECE 和混淆矩阵，并提供发布门禁阈值。
  - 交付：`knowledge/calibration.py`、`tests/fixtures/mastery_calibration.json`、`tests/test_calibration.py`。

### 4E：P1 运维与可观测性

- [x] **4E-1 结构化日志**
  - JSON 格式、call site、错误分类、敏感信息脱敏
  - 统一 `logging` 配置入口
  - (done: src/nexus/log_config.py — _JSONFormatter 与 configure_logging)

- [x] **4E-2 运行状态查询**
  - pipeline/session/backup 最近运行状态
  - (done: read-only `nexus status --json`, scheduler reports, session backlog and queue counts)

- [x] **4E-3 备份策略**
  - 保留、验证、导出和清理策略
  - 扩展 `nexus db-backup`
  - (done: src/nexus/cli/db_backup.py — `--retain` 保留策略；src/nexus/knowledge/maintenance.py — create_backup/verify_backup/restore_backup)

- [x] **4E-4 失败队列**
  - 重试上限及人工处理命令
  - 从 archive-only 扩展到 aggregation + ingest
  - (done: shared bounded-retry JSON queues with `.dead` manual-review files for archive, aggregation, and ingest)

- [x] **4E-5 并发 soak tests**
  - 长时间运行、并发 ingest/QA/backup 测试
  - 交付：`soak/test_concurrency_soak.py`、`make soak`、`make soak-long`；覆盖 SQLite WAL、共享 ingest lock、独占 backup 和 single-flight sync。

### 4F：P2 质量门禁与发布

- [x] **4F-0 RC 测试门禁与 clean-room 启动验收**
  - 默认 pytest 只收集正式 `tests/`，临时 `tmp/` harness 不阻塞发布门禁。
  - 覆盖离线 pipeline、doctor、status、db-check 的全新数据目录验收。
  - 交付：`pyproject.toml` pytest 配置、`tests/test_release_smoke.py`、测试指南更新。

- [x] **4F-1 扩展 retrieval golden set**
  - 多课程、中英文、无答案、跨课程
  - (done: 20-case offline golden fixture spans four courses, Chinese/English, no-answer and unknown-course cases)

- [x] **4F-2 QA citation gate**
  - 运行时 source-ID/required-citation gate：未知或缺失引用降级为有来源的课件摘录。
  - 语义蕴含：`validate_citation_support` 将带 `[Sn]` 的结论绑定到对应 chunk.content，规则覆盖不足则同样降级；可选 `nli_fn` 失败回落规则。
  - 交付：`context_assembler.validate_citation_support`、`QAPipeline.ask` 串联、`tests/test_qa_citation_gate.py` 走真实 `ask()`。

- [x] **4F-3 Assessment 案例扩展**
  - 正确、部分正确、错误、遗忘、提示注入和课件冲突案例。
  - 交付：`tests/fixtures/assessment_cases.json` 与数据驱动的评分边界、证据绑定和 mastery 事件契约测试。

- [x] **4F-4 数据库迁移与备份恢复兼容测试**
  - (done: schema migration concurrency, maintenance backup/restore, and safe migration tests)

- [x] **4F-5 文档更新**
  - README、Reference、Testing、迁移指南
  - (done: 2026-07-24 reconciliation documents current LLM enablement, runtime status, queues, and test count)

- [~] **4F-6 发布 0.3.0 beta 候选版本**
  - 当前版本 `0.3.0b2`；自动 clean-room、wheel 和调度同步门禁已接入，待真实 connector/scheduler 验收。

---

### 执行顺序

```
4A → 4B → 4C → 4D → 4E → 4F
```

- 4A 不阻塞其他但最先做（后续 CI 验收依赖）
- 4B 在 4C 前（先清旧数据再统一写入路径）
- 4C 在 4D 前（Progress 输入来自入库管线）
- 4D 和 4E 可部分并行（Progress 重构和日志/备份互不依赖）
- 4E-1（结构化日志）提前到 4A 作为 Doctor 增强的一部分
- 4D-7（离线校准集）移到 4F 与 golden set 一起
- 4F 串行最后

---

## 约定（所有 agent 必读）

- 运行：`PYTHONPATH=src` 前缀（无 pyproject.toml 安装）；`make test` / `make ui` 已就绪。
- subprocess 调用须设 `env={"PYTHONPATH": "src/", **os.environ}`。
- stdout = JSON only；调试日志 → stderr。
- `Task.from_dict` 期望 `due_at` 为 ISO 字符串；scraper 返回 datetime 须先转换。
- LLM 后端：Qwen via DashScope；key 在 `data/QWEN_API_KEY.json`，env `QWEN_API_KEY`。
- 不在未触碰代码上加多余注释/docstring；方案最小聚焦。
- 提交：每轮开 `nexus/<task-id>-<slug>` 分支，commit message 末尾加 `Co-Authored-By: Claude <noreply@anthropic.com>`。
