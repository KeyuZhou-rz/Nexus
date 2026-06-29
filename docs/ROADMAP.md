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

- [ ] **T4 briefing 单元覆盖**
  - 现状：仅 `test_briefing_context.py`。
  - 补：`select_exam_reminders` 窗口边界、`_is_noise` 过滤、`_rule_briefing` 降级路径、`_normalize_title`/`_contains_cjk`。
  - 文件：`tests/test_briefing_unit.py`（新建）、`src/nexus/intelligence/briefing.py`

- [ ] **T5 dashboard view_model 错误路径**
  - 现状：`tests/test_dashboard_view_model.py` 85 行，覆盖正向。
  - 补：空任务、缺失字段、时区边界。
  - 文件：`tests/test_dashboard_view_model.py`、`src/nexus/dashboard/view_model.py`

---

## Phase M — 记忆地基（U/B 的地基）

**决策（已冻结）**：分层不合并 —— `knowledge/`(SQLite, Ebbinghaus, 概念图谱) 为主存储；根级 `memory_*` 降级为别名词典 + 零成本弱项兜底；`state.json` 不再生成（已无数据，迁移风险≈0）。concept ID 对齐走 `concept_resolver` 三级降级（别名表→规范化→模糊匹配≥0.7）。图谱填充从 Chroma 70 chunks 回填 SQLite `chunks` 表 + 跑 `concept_extractor`，不重跑 LLM 解析。

- [ ] **M1-1 设计冻结**（design，无代码）：本决策写入 ROADMAP，标 M1 `[~]`。验收：96 绿。
- [ ] **M1-2 SQLite chunks 回填**：新建 `knowledge/chunk_backfill.py` + `cli/concepts_backfill.py`，从 Chroma 回填 SQLite `chunks` 表 + 补 `documents`。验收：`SELECT COUNT(*) FROM chunks` ≥ 47。测试 `tests/test_chunk_backfill.py`。
- [ ] **M1-3 别名表 + concept_resolver**：`schema.sql` 加 `concept_aliases`(alias/course_id/concept_id/source, UNIQUE(alias,course_id))；新建 `concept_resolver.py` 三级 `resolve`；`sqlite_store` 加 `upsert_alias`/`get_alias`。测试 `tests/test_concept_resolver.py`。
- [ ] **M1-4 跑通 concept_extractor 填充图谱**（核心）：扩展回填 CLI，回填后调 `ConceptExtractor.extract_from_chunks → refine_with_llm(有 key) → persist`。验收：`concepts`>0、`chunk_concepts`>0、`GraphRetriever.retrieve([chunk_id])` 返 `has_content=True`。测试 `tests/test_concept_backfill.py`（stub LLM）。
- [ ] **M1-5 统一写口 memory_writer**：新建 `memory_writer.py`，`record_weak_point`(resolve→add_weak_point，命中记 struggled) + `apply_feedback_unified`(reject→remove+forgot，accept→mastered+correct_answer)。测试 `tests/test_memory_writer.py`。
- [ ] **M1-6 三个根级入口改走 memory_writer**：改 `cli/memory_extract.py`、`cli/memory_feedback.py`、`archive_sync/post_ingest.py`，移除 state.json 读写。验收：`grep -r "state.json" src/nexus/` 仅剩 state_store.py；`test_archive_post_ingest` 改断言到 SQLite user_profile。
- [ ] **M1-7 UnifiedMemoryReader + briefing 改造**：新建 `knowledge/unified_memory.py`(`weak_points`/`review_queue`=get_decaying_concepts/`low_mastery_topics`)；`briefing._inject_learning_context` 用 reader 替代 `load_state`。验收：`grep load_state briefing.py` 无残留；`test_briefing_context` 改 monkeypatch reader。B1 前置。
- [ ] **M1-8 弃用标记 + 文档**：`state_store.py`/`memory_update.py` 加 deprecation 注释（保留 `memory_extractor`）；ROADMAP M1 标 `[x]`。验收：96 绿。

**风险**：M1-2/3/4 纯新增不碰现有测试；M1-6/7 高风险（同步改 `test_archive_post_ingest`/`test_briefing_context` 断言，语义保留）；数据改动前 `cp data/nexus.db data/nexus.db.bak`。

---

## Phase B — 简报系统（M1-7 后扩展）

- [ ] **B1 briefing 注入 temporal 掌握度**
  - `_inject_learning_context` 显式接入 `temporal_memory.get_decaying_concepts`（"今日该复习什么"）。M1-7 已切读 UnifiedMemoryReader，B1 在此基础上注入 decaying 概念。
  - 文件：`src/nexus/intelligence/briefing.py`、`src/nexus/knowledge/temporal_memory.py`

- [ ] **B2 LLM 简报失败降级测试**
  - `_llm_briefing` 异常/超时 → `_rule_briefing` 兜底，确保不抛。
  - 文件：`tests/test_briefing_fallback.py`（新建）

- [ ] **B3 考试提醒窗口边界**
  - `select_exam_reminders(window_days=180)` 边界、跨年、无考试。
  - 文件：`tests/test_briefing_unit.py`

---

## Phase L — LLM 统一网关（M 后）

- [ ] **L1 抽 `knowledge/llm_gateway.py` 单例**：收敛 7 处硬编码 Qwen（`query_engine`/`session_manager`/`concept_extractor`×2/`parser` + `intelligence/llm.py`），统一读 `NEXUS_LLM_MODEL`/`NEXUS_LLM_API_KEY`/`NEXUS_LLM_BASE_URL`，Qwen 默认可换 OpenAI/Claude。
- [ ] **L2 key 管理统一**：合并 `QWEN_API_KEY.json` 与 `NEXUS_LLM_API_KEY`。
- [ ] **L3 交互日志统一**：`log_interaction` 覆盖简报侧。

## Phase U — 辅导深度（依赖 M+L）

- [ ] **U1 苏格拉底引导**：改写 `prompts.QA_SYSTEM_PROMPT`，基于 mastery 低位/weak_point 命中触发分步追问；`context_assembler` 按 mastery 选引导策略。
- [ ] **U2 学习计划生成**：新建 `knowledge/study_planner.py`，LLM 结合考试日(Calendar) + 掌握度(temporal_memory) + 前置关系(graph_retriever 拓扑序) 生成周计划。
- [ ] **U3 掌握度可视化**：Streamlit 加遗忘曲线图 + 概念图谱 + 学习计划视图（消费 `get_mastery_overview`/`get_decaying_concepts`）。

## Phase E — 邮件重要信息（依赖 L）

- [ ] **E1 Gmail 全文拉取**：`format="full"` + MIME 解析拿正文。
- [ ] **E2 LLM 重要性/结构化提取**：考试时间/截止/行动项 → 结构化字段，非 snippet 截断。
- [ ] **E3 课程归属修复 + 分页**：`course_aliases.json` 缺失问题 + 时间窗口/分页拉取。

## Phase X — LMS 抽象（独立，最后）

- [ ] **X1 抽 `aggregators/lms_base.py` 接口**：courses/assignments/announcements/files。
- [ ] **X2 Brightspace 适配为实现**：archive_sync + iCal/RSS 路径。
- [ ] **X3 新建 `aggregators/canvas.py`**：Canvas REST API + OAuth token。

---

## 约定（所有 agent 必读）

- 运行：`PYTHONPATH=src` 前缀（无 pyproject.toml 安装）；`make test` / `make ui` 已就绪。
- subprocess 调用须设 `env={"PYTHONPATH": "src/", **os.environ}`。
- stdout = JSON only；调试日志 → stderr。
- `Task.from_dict` 期望 `due_at` 为 ISO 字符串；scraper 返回 datetime 须先转换。
- LLM 后端：Qwen via DashScope；key 在 `data/QWEN_API_KEY.json`，env `QWEN_API_KEY`。
- 不在未触碰代码上加多余注释/docstring；方案最小聚焦。
- 提交：每轮开 `nexus/<task-id>-<slug>` 分支，commit message 末尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
