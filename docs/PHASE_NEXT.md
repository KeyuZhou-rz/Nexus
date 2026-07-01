# Phase Next — LLM 成本优化与基础设施统一

> 目标：在启动 Web 应用迁移前，完成 LLM 层的统一、缓存和降本，以及记忆系统的 API 化。
> 顺序：LLM 网关统一 → 文档解析缓存 → 查询扩展本地化 → 记忆系统 API 化

---

## Task N1: LLM 网关统一

### 现状问题

Nexus 有 7 个 LLM 调用点，但存在 **3 套平行的 LLM 客户端**，没有统一的成本追踪和调用审计：

| # | 调用点 | 文件 | 使用客户端 | 模型 |
|---|--------|------|-----------|------|
| 1 | 文档解析 | `knowledge/parser.py` | 直接调 DashScope Files API | qwen-long |
| 2 | 概念提取 | `knowledge/concept_extractor.py` | `llm_gateway.chat()` ✅ | deepseek-chat |
| 3 | 每日简报 | `intelligence/briefing.py` | `intelligence/llm.py` 的 `LLMClient` ❌ | deepseek-chat |
| 4 | 邮件提取 | `aggregators/google.py` | `llm_gateway.chat()` ✅ | deepseek-chat |
| 5 | 会话分析 | `knowledge/session_manager.py` | `llm_gateway.chat()` ✅ | deepseek-chat |
| 6 | QA 问答 | `knowledge/qa_pipeline.py` | `llm_gateway.chat()` ✅ | deepseek-chat |
| 7 | 查询扩展 | `knowledge/query_engine.py` | `llm_gateway.chat()` ✅ | deepseek-chat |

**核心问题**：
1. `intelligence/llm.py` 的 `LLMClient` 是一套独立的 REST 客户端，与 `llm_gateway.py` **完全平行**，配置路径不同（读 `AppConfig.llm_*` 而非网关的 `NEXUS_LLM_*` 环境变量）
2. `parser.py` 走 Qwen DashScope Files API，完全不经过网关，没有统一缓存层
3. 没有统一的调用点标识（call_site tag），无法追踪每个调用点的成本
4. 没有统一的速率限制和重试逻辑

### 目标

**所有 7 个调用点通过 `llm_gateway.py` 统一出口**，网关负责：
- 模型选择和 API key 解析
- 调用点标识（`call_site` 参数，用于日志和成本追踪）
- 统一的 `chat()` 和 `structured_output()` 接口
- litellm 兼容的响应格式
- 每个调用点的调用计数和 token 估算（日志到 `interaction_logs`）
- 优雅降级（LLM 不可用时不抛异常，业务层自行处理）

### 关键数据管道与数据结构

Nexus 的数据流全景：

```
┌─────────────────────────────────────────────────────────────┐
│                        数据摄入层                              │
│                                                              │
│  Brightspace iCal ──┐                                        │
│  Brightspace RSS  ──┤                                        │
│  Google Calendar ───┼──▶ Aggregator.fetch_tasks() ──▶ Task[] │
│  Gmail ─────────────┤                                        │
│  Canvas REST API ───┘                                        │
│                                                              │
│  Brightspace HTML ──▶ Playwright Scraper ──▶ Archive files   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                        Pipeline 层                            │
│                                                              │
│  pipeline.py: run_mvp_pipeline()                             │
│    1. archive_sync (子进程 Playwright)                        │
│    2. aggregation (所有 Aggregator → tasks.json)              │
│    3. briefing (tasks.json → briefing.json)                  │
│    4. knowledge_ingest (archive files → ChromaDB + SQLite)   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                        知识层                                 │
│                                                              │
│  PDF/DOCX ──▶ parser.py ──▶ ParsedChunk[]                   │
│                (Qwen-long LLM 语义分块)                        │
│                          │                                    │
│                          ▼                                    │
│              chunking.py ──▶ text chunks                     │
│                          │                                    │
│                          ▼                                    │
│              embedding.py ──▶ ChromaDB vector store           │
│                          │                                    │
│                          ▼                                    │
│              concept_extractor.py ──▶ concepts + relations    │
│                          │         (写入 SQLite)              │
│                          ▼                                    │
│              graph_retriever.py ──▶ 知识图谱检索               │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                       记忆层                                  │
│                                                              │
│  temporal_memory.py: 艾宾浩斯遗忘曲线                          │
│    - record_event(concept_id, event_type, course_id)         │
│    - get_decaying_concepts(course_id) → urgency tiers        │
│    - get_mastery(concept_id) → float 0.0-1.0                 │
│                                                              │
│  user_profile.py: 用户画像                                    │
│    - weak_points / mastered / learning_style                 │
│                                                              │
│  unified_memory.py: 组合阅读器                                │
│    - weak_points + decaying_concepts + low_mastery_topics    │
│                                                              │
│  memory_writer.py: 统一写入入口                               │
│    - record_weak_point() / apply_feedback_unified()          │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                       输出层                                  │
│                                                              │
│  briefing.py ──▶ briefing.json (每日简报)                     │
│  qa_pipeline.py ──▶ 课程问答                                  │
│  study_planner.py ──▶ 7日学习计划                             │
│  streamlit_app.py ──▶ 用户界面                                │
└─────────────────────────────────────────────────────────────┘
```

**核心数据结构**：

```python
# models.py
@dataclass
class Task:
    id: str              # "gmail:abc123" | "gcal:def456" | "brightspace:ghi789"
    title: str           # 显示标题
    due_at: datetime|None # 截止时间
    source: str           # "gmail"|"gcal"|"brightspace"|"canvas"
    url: str|None
    course: str|None     # 关联课程
    status: str          # "open"|"done"|"snoozed"
    priority: int        # 0=normal, 1=medium, 3=high
    tags: list[str]      # ["email","unread","course_notification","deadline"]
    snippet: str|None    # 摘要文本
    received_at: datetime|None

# knowledge/schema.sql 关键表
chunks(id, document_id, content, embedding_metadata)
concepts(id, name, course_id, description)
concept_relations(concept_id, related_id, relation_type)
chunk_concepts(chunk_id, concept_id, relevance)
concept_mastery(concept_id, user_id, mastery_score, last_reviewed, event_count, decay_rate)
learning_events(concept_id, event_type, timestamp, session_id)
user_profile(user_id, profile_data JSON, updated_at)
interaction_logs(session_id, user_query, llm_response, model_used, cost_tracking)
```

### 实现步骤

- [ ] **N1.1** 扩展 `llm_gateway.py`：
  - 添加 `chat(messages, call_site, temperature, max_tokens) -> LLMResponse` 方法（替代当前仅提供 `litellm_kwargs` 的半成品状态）
  - 添加 `structured_output(messages, schema, call_site) -> dict` 方法
  - 添加 `is_configured()` 检查（已有，确认覆盖）
  - 添加 `call_site` 参数用于成本归属和日志
  - 添加调用计数和 token 估算（tiktoken 或字符估算）

- [ ] **N1.2** 改造 `intelligence/llm.py` 的 `LLMClient`：
  - `chat()` 改为调用 `llm_gateway.chat(call_site="briefing")`
  - 或直接删除 `LLMClient`，让 `briefing.py` 直接使用网关
  - 保留 `LLMResponse` dataclass 和 `LLMError` 异常（作为网关的返回类型）
  - 保留 `extract_json()` 工具函数（非 LLM 相关）

- [ ] **N1.3** 改造 `parser.py`：
  - 添加 `call_site="document_parse"` 的网关路由
  - Qwen-long 不走 OpenAI 兼容 API（DashScope Files API 是独立的），所以解析器保留自己的逻辑，但：
    - 在调用前检查网关的 `is_configured()`
    - 通过网关的日志钩子记录解析调用
    - 在网关注册 `call_site="document_parse"` 用于成本追踪

- [ ] **N1.4** 添加 `interaction_logs` 统一写入：
  - 所有 7 个调用点写入 `interaction_logs` 表时带上 `call_site` 字段
  - `schema.sql` 的 `interaction_logs` 表已有 `model_used`、`input_tokens`、`output_tokens` 字段，确认 `call_site` 是否需要加列

- [ ] **N1.5** 测试：
  - `test_llm_gateway.py`（新建）：测试网关的配置解析、回退、调用点标识
  - 修改 `test_briefing_fallback.py`：适配新的网关调用路径
  - 修改 `test_gmail_enhanced.py`：确认邮件提取的网关路径不变

**文件**：`src/nexus/knowledge/llm_gateway.py`、`src/nexus/intelligence/llm.py`、`src/nexus/intelligence/briefing.py`、`src/nexus/knowledge/parser.py`

---

## Task N2: 文档解析缓存

### 现状问题

`parser.py` 的 `_parse_with_qwen()` 对每个文档调用 Qwen-long（DashScope Files API），这是最贵的 LLM 调用（$0.05-0.15/文档）。相同文件重复摄入时没有缓存，会重新调用。

### 目标

基于文件内容的 SHA-256 哈希做缓存。缓存键 = `(file_hash, course_id, parse_mode)`，缓存值 = `ParsedChunk[]`（序列化为 JSON）。

### 实现步骤

- [ ] **N2.1** 在 SQLite `schema.sql` 中添加 `parse_cache` 表：
  ```sql
  CREATE TABLE IF NOT EXISTS parse_cache (
      file_hash TEXT NOT NULL,        -- SHA-256 of file content
      course_id TEXT NOT NULL,
      parse_mode TEXT NOT NULL DEFAULT 'qwen-long',
      chunks_json TEXT NOT NULL,       -- JSON-serialized ParsedChunk[]
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (file_hash, course_id, parse_mode)
  );
  ```

- [ ] **N2.2** 在 `sqlite_store.py` 中添加缓存读写方法：
  - `get_parse_cache(file_hash, course_id) -> list[ParsedChunk] | None`
  - `set_parse_cache(file_hash, course_id, chunks: list[ParsedChunk]) -> None`

- [ ] **N2.3** 在 `parser.py` 中集成缓存：
  - `parse_document()` 开头先计算文件 SHA-256
  - 检查 `parse_cache` 表，命中则直接返回
  - 未命中 → 调用 Qwen-long → 写入缓存 → 返回

- [ ] **N2.4** 添加缓存失效机制：
  - 提供 CLI 命令 `nexus parse-cache clear --course <id>` 清除指定课程的缓存
  - `parse_cache` 表添加 `file_size` 列用于审计

- [ ] **N2.5** 测试：
  - `test_parse_cache.py`（新建）：测试缓存命中/未命中、缓存清空、哈希碰撞边界

**文件**：`src/nexus/knowledge/parser.py`、`src/nexus/knowledge/sqlite_store.py`、`src/nexus/knowledge/schema.sql`

---

## Task N3: 查询扩展本地化

### 现状问题

`query_engine.py` 中每次用户提问都会调用 LLM（deepseek-chat）做查询扩展。这是频率最高的 LLM 调用，但 token 量极小（~256 tokens）。用已有的 `all-MiniLM-L6-v2` 嵌入模型或纯规则方法替代，成本降为 0。

### 目标

实现双层查询扩展：
1. **规则层**（默认）：关键词提取 + 课程别名展开 + 同义词映射
2. **LLM 层**（可选，Pro 用户）：语义查询重写

### 实现步骤

- [ ] **N3.1** 在 `query_engine.py` 中实现 `_expand_query_rule(query, course_id) -> list[str]`：
  - 按标点/空格分词提取关键词
  - 通过 `concept_resolver` 展开别名和同义词
  - 组合生成 2-3 个查询变体
  - 不依赖任何 LLM

- [ ] **N3.2** 保留 `_expand_query_llm()` 作为可选增强（Pro 用户）：
  - 仅当 `use_llm_expansion=True` 且 `is_configured()` 时调用
  - 结果与规则扩展结果合并去重

- [ ] **N3.3** 在 `llm_gateway` 中标志 `call_site="query_expansion"` 为 optional：
  - 网关可以配置各调用点的启用/禁用
  - Free 用户该调用点始终禁用

- [ ] **N3.4** 测试：
  - `test_query_expansion.py`（新建）：验证规则扩展的关键词覆盖、别名展开、无 LLM 降级

**文件**：`src/nexus/knowledge/query_engine.py`、`src/nexus/knowledge/concept_resolver.py`

---

## Task N4: 记忆系统 API 化

### 现状问题

`temporal_memory.py`、`unified_memory.py`、`user_profile.py`、`study_planner.py` 是 Nexus 的核心差异化能力，但：
- 没有统一的对外 API 接口
- 各模块通过直接实例化相互调用
- Streamlit UI 中只有零散的展示
- 无法为未来的 Web 前端提供清晰的 API 契约

### 目标

创建一个 `MemoryAPI` 门面类，为所有记忆相关操作提供统一接口，为后续 Web 前端（Next.js）提供清晰的 API 边界。

### 实现步骤

- [ ] **N4.1** 新建 `src/nexus/knowledge/memory_api.py`：
  ```python
  class MemoryAPI:
      """记忆系统的统一 API 门面"""
      
      # 掌握度
      def get_mastery_overview(user_id, course_id) -> MasteryOverview
      def get_decaying_concepts(user_id, course_id, limit=10) -> list[ConceptMastery]
      def record_review_event(user_id, concept_id, event_type) -> None
      
      # 用户画像
      def get_profile(user_id, course_id) -> UserProfile
      def update_profile(user_id, course_id, patch: dict) -> None
      
      # 弱点分析
      def get_weak_points(user_id, course_id) -> list[WeakPoint]
      def get_learning_recommendations(user_id, course_id) -> list[Recommendation]
      
      # 学习计划
      def generate_study_plan(user_id, course_id, days=7) -> StudyPlan
      
      # 遗忘曲线
      def get_forgetting_curve(user_id, concept_id) -> ForgettingCurveData
      def get_predicted_forgetting_alerts(user_id, course_id) -> list[Alert]
  ```

- [ ] **N4.2** 重构现有调用点：
  - `briefing.py` 中的 `UnifiedMemoryReader` 改为通过 `MemoryAPI` 调用
  - `streamlit_app.py` 中的学习进度 tab 改为通过 `MemoryAPI` 调用
  - `qa_pipeline.py` 中的苏格拉底引导改为通过 `MemoryAPI` 获取 mastery

- [ ] **N4.3** 添加 JSON 序列化支持：
  - 所有 API 返回的数据类支持 `to_dict()` 方法
  - 为未来的 FastAPI REST 端点做准备

- [ ] **N4.4** 测试：
  - `test_memory_api.py`（新建）：测试所有 API 方法
  - 修改 `test_briefing_context.py`：适配新的 API 路径

**文件**：`src/nexus/knowledge/memory_api.py`（新建）、`src/nexus/intelligence/briefing.py`、`src/nexus/streamlit_app.py`、`src/nexus/knowledge/qa_pipeline.py`

---

## 依赖关系

```
N1 (LLM网关统一)
 ├── N2 (文档解析缓存，依赖网关的缓存基础设施)
 ├── N3 (查询扩展本地化，依赖网关的 call_site 启用/禁用)
 └── N4 (记忆系统 API 化，独立，可与 N1 并行)
```

## 验证方式

- `make test` 全绿，不引入回归
- 每个 Task 有对应的新测试文件
- 手动运行 `make ui` 确认 Streamlit 仪表板正常
- LLM 调用日志中可追溯到具体 call_site
