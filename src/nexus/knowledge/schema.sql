-- Nexus Phase 3: SQLite Schema
-- 所有表均支持幂等创建（IF NOT EXISTS）
-- WAL 模式和外键约束须在每次连接时手动启用（见 sqlite_store.py）

CREATE TABLE IF NOT EXISTS schema_migrations (
    version         INTEGER PRIMARY KEY,
    applied_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ────────────────────────────────────────────────
-- 文档注册表
-- 追踪已解析的文件，用于幂等性检查（file_hash）和增量更新
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,      -- "{course_id}/{filename}"，全局唯一标识
    course_id       TEXT NOT NULL,         -- 课程代码，如 "CS202_OS"
    filename        TEXT NOT NULL,         -- 原始文件名
    file_hash       TEXT NOT NULL,         -- 文件 MD5，用于检测文件是否更新
    parsed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    chunk_count     INTEGER DEFAULT 0,     -- 成功入库的 chunk 数量
    parse_model     TEXT,                  -- 解析用模型，如 "gemini-2.0-flash"
    parse_status    TEXT DEFAULT 'pending', -- pending | parsing | done | failed
    status_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ────────────────────────────────────────────────
-- Chunk 结构化索引
-- 与 ChromaDB 中的向量 chunk 对应，存储结构化元数据
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,      -- 与 ChromaDB document id 保持一致
    document_id     TEXT NOT NULL,         -- 对应 documents.id
    course_id       TEXT NOT NULL,
    topic           TEXT NOT NULL,         -- 知识点标题
    type            TEXT,                  -- definition | algorithm | example | theorem | overview | code | exercise
    keywords        TEXT,                  -- JSON 数组，["kw1", "kw2"]
    prerequisites   TEXT,                  -- JSON 数组
    page            INTEGER,               -- 源文档页码
    lecture_number  INTEGER,               -- lecture 编号（从文件名解析，可为空）
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- ────────────────────────────────────────────────
-- 用户画像
-- key-value 存储，value 为 JSON 字符串
-- 例: key="profile", value={"courses": [...], "weak_points": [...]}
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_profile (
    key             TEXT PRIMARY KEY,
    value           TEXT,                  -- JSON 字符串
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ────────────────────────────────────────────────
-- 交互日志
-- 每次问答记录一条，用于 session 结束后分析并更新用户画像
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS interaction_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id      TEXT NOT NULL,
    course_id       TEXT,
    user_query      TEXT NOT NULL,
    chunks_used     TEXT,                  -- JSON 数组，chunk_id 列表
    llm_response    TEXT,
    model_used      TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER
);

-- ────────────────────────────────────────────────
-- 外部数据源同步状态
-- 记录 Brightspace / Google Calendar 最近同步时间和结果
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sync_metadata (
    source          TEXT PRIMARY KEY,      -- "brightspace" | "google_calendar"
    last_sync       TIMESTAMP,
    status          TEXT,                  -- "ok" | "failed"
    details         TEXT                   -- JSON，存储错误信息或统计数据
);

-- ────────────────────────────────────────────────
-- 活跃 Session 状态持久化
-- 修复：原先只存内存 dict，Streamlit/进程重启即丢失，idle/end_session
-- 触发的 profile 更新因此可能永远不会发生。现在落盘，session 结束后删除行。
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS active_sessions (
    session_id         TEXT PRIMARY KEY,
    started_at         TIMESTAMP NOT NULL,
    last_active        TIMESTAMP NOT NULL,
    course_id          TEXT,
    interaction_count  INTEGER DEFAULT 0,
    ended              INTEGER DEFAULT 0      -- 0/1，见 session_manager.py 说明
);

-- ────────────────────────────────────────────────
-- 索引：加速常用查询路径
-- ────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_chunks_course    ON chunks(course_id);
CREATE INDEX IF NOT EXISTS idx_chunks_lecture   ON chunks(course_id, lecture_number);
CREATE INDEX IF NOT EXISTS idx_chunks_type      ON chunks(type);
CREATE INDEX IF NOT EXISTS idx_logs_session     ON interaction_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_logs_course      ON interaction_logs(course_id);
CREATE INDEX IF NOT EXISTS idx_logs_session_time ON interaction_logs(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_course_time  ON interaction_logs(course_id, timestamp);

-- ════════════════════════════════════════════════
-- Phase 4: Knowledge Graph — 概念图谱 + 时间记忆
-- ════════════════════════════════════════════════

-- ────────────────────────────────────────────────
-- 概念节点
-- 从 chunk 的 keywords / prerequisites / topic 中提取的知识概念
-- concept_id 格式: "{course_prefix}__{slug}"，如 "cs202_os__deadlock"
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS concepts (
    concept_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,           -- 显示名，如 "死锁 / Deadlock"
    course_id       TEXT NOT NULL,           -- 来源课程
    definition      TEXT,                    -- LLM 生成的一句话定义
    concept_type    TEXT,                    -- fundamental | applied | technique | theorem
    first_seen_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ────────────────────────────────────────────────
-- 概念间关系（有向边）
-- 同课程内: prerequisite / extends / applied_in / contradicts
-- 跨课程:   analogous_to / prerequisite / extends / applied_in
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS concept_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    relation_type   TEXT NOT NULL,           -- prerequisite | analogous_to | extends | applied_in | contradicts
    confidence      REAL DEFAULT 1.0,        -- 0.0-1.0
    evidence        TEXT,                    -- 简要理由
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES concepts(concept_id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES concepts(concept_id) ON DELETE CASCADE,
    UNIQUE(source_id, target_id, relation_type)
);

-- ────────────────────────────────────────────────
-- Chunk ↔ Concept 多对多关联
-- 一个 chunk 可包含多个概念，一个概念可跨多个 chunk
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunk_concepts (
    chunk_id        TEXT NOT NULL,
    concept_id      TEXT NOT NULL,
    relevance       REAL DEFAULT 1.0,        -- 该概念在此 chunk 中的核心程度
    PRIMARY KEY (chunk_id, concept_id),
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    FOREIGN KEY (concept_id) REFERENCES concepts(concept_id) ON DELETE CASCADE
);

-- ────────────────────────────────────────────────
-- 学习事件流水
-- 每次 Q&A 交互记录涉及概念的掌握信号
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS learning_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id      TEXT NOT NULL,
    event_type      TEXT NOT NULL,           -- correct_answer | struggled | reviewed | forgot
    session_id      TEXT,
    context         TEXT,                    -- JSON: {question, confidence}
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (concept_id) REFERENCES concepts(concept_id) ON DELETE CASCADE
);

-- ────────────────────────────────────────────────
-- 概念掌握度（Ebbinghaus 遗忘曲线参数）
-- mastery(t) = initial_mastery * e^(-decay_rate * days_since_review)
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS concept_mastery (
    concept_id      TEXT PRIMARY KEY,
    initial_mastery REAL DEFAULT 0.0,        -- 上次学习事件后的掌握度
    decay_rate      REAL DEFAULT 0.3,        -- 个性化衰减速率
    last_reviewed   TIMESTAMP,
    review_count    INTEGER DEFAULT 0,
    streak          INTEGER DEFAULT 0,       -- 连续正确次数
    FOREIGN KEY (concept_id) REFERENCES concepts(concept_id) ON DELETE CASCADE
);

-- ────────────────────────────────────────────────
-- 概念别名表 (Phase M1-3)
-- 用户可能用不同的名称指代同一个概念，此处记录规范化映射
-- source: manual | memory | llm_extracted | cross_course
-- UNIQUE(alias, course_id) 保证同一课程内别名不重复
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS concept_aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alias           TEXT NOT NULL,
    course_id       TEXT NOT NULL,           -- 该别名所属课程上下文
    concept_id      TEXT NOT NULL,           -- 映射到的规范概念 ID
    source          TEXT DEFAULT 'manual',   -- manual | memory | llm_extracted | cross_course
    confidence      REAL DEFAULT 1.0,        -- 别名映射置信度
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (concept_id) REFERENCES concepts(concept_id) ON DELETE CASCADE,
    UNIQUE(alias, course_id)
);

-- ────────────────────────────────────────────────
-- 文档解析缓存 (Phase N2)
-- 按文件内容哈希 + 课程 ID 缓存 LLM 解析结果，避免重复文档重复调用 LLM
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS parse_cache (
    file_hash   TEXT NOT NULL,           -- SHA-256 of file content
    course_id   TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL,
    model_version  TEXT NOT NULL,
    prompt_hash    TEXT NOT NULL,
    parse_method   TEXT NOT NULL,
    chunks_json TEXT NOT NULL,           -- JSON-serialized ParsedChunk[]
    file_size   INTEGER,                 -- 审计用，不参与缓存键
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (file_hash, course_id, parser_version, model_version, prompt_hash)
);

-- Phase 4 索引
CREATE INDEX IF NOT EXISTS idx_aliases_course         ON concept_aliases(course_id);
CREATE INDEX IF NOT EXISTS idx_aliases_concept        ON concept_aliases(concept_id);
CREATE INDEX IF NOT EXISTS idx_concepts_course          ON concepts(course_id);
CREATE INDEX IF NOT EXISTS idx_relations_source         ON concept_relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_target         ON concept_relations(target_id);
CREATE INDEX IF NOT EXISTS idx_chunk_concepts_concept   ON chunk_concepts(concept_id);
CREATE INDEX IF NOT EXISTS idx_learning_events_concept  ON learning_events(concept_id);
CREATE INDEX IF NOT EXISTS idx_learning_events_time     ON learning_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_learning_events_concept_time ON learning_events(concept_id, timestamp);

-- ════════════════════════════════════════════════
-- Phase N: Memory-Layer Enhancement
-- 知识漏洞扫描 + 冲刺计划 + 考后复盘
-- ════════════════════════════════════════════════

-- ────────────────────────────────────────────────
-- 周扫描快照：每次 LLM 扫描生成一份 checkpoint
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS concept_checkpoints (
    checkpoint_id   TEXT PRIMARY KEY,          -- "{course_id}__{year}-W{week_num}"
    course_id       TEXT NOT NULL,
    week_start      TEXT NOT NULL,
    week_end        TEXT NOT NULL,
    generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ────────────────────────────────────────────────
-- checkpoint 中的单个概念（带自测题）
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS checkpoint_concepts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_id   TEXT NOT NULL,
    concept_id      TEXT,                      -- 可关联到已有 concepts 表
    name            TEXT NOT NULL,
    importance      INTEGER DEFAULT 5,         -- 1-10
    self_test_q1    TEXT NOT NULL,
    self_test_q2    TEXT NOT NULL,
    self_test_q3    TEXT NOT NULL,
    FOREIGN KEY (checkpoint_id) REFERENCES concept_checkpoints(checkpoint_id) ON DELETE CASCADE
);

-- ────────────────────────────────────────────────
-- 概念查询追踪：每次 Q&A 记录涉及概念 + 犹豫信号
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS concept_query_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id      TEXT,
    concept_name    TEXT NOT NULL,
    session_id      TEXT,
    query_text      TEXT,
    had_hesitation  INTEGER DEFAULT 0,          -- 0/1
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ────────────────────────────────────────────────
-- 考试记录：用户录入的考试元数据
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exam_records (
    exam_id         TEXT PRIMARY KEY,           -- "{course_id}__{exam_date}"
    course_id       TEXT NOT NULL,
    exam_date       TEXT NOT NULL,
    chapter_weights TEXT,                       -- JSON: {"ch3": 0.15, ...}
    user_input      TEXT,                       -- 用户考后反馈
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ────────────────────────────────────────────────
-- 考后复盘报告：LLM 生成的得失分析
-- ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exam_retrospectives (
    retrospective_id TEXT PRIMARY KEY,          -- "retro__{exam_id}"
    exam_id         TEXT NOT NULL,
    predicted_weak   TEXT,                      -- JSON: 考前预测的薄弱概念
    actual_hits      TEXT,                      -- JSON: 实际考到的薄弱概念
    blind_spots      TEXT,                      -- JSON: 未预料的丢分概念
    overestimated   TEXT,                      -- JSON: 以为掌握但丢分的概念
    analysis_text   TEXT,                      -- LLM 生成的完整分析文本
    retention_hook   TEXT,                      -- 记忆锚点/口诀
    generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (exam_id) REFERENCES exam_records(exam_id)
);

-- Phase N 索引
CREATE INDEX IF NOT EXISTS idx_checkpoints_course     ON concept_checkpoints(course_id);
CREATE INDEX IF NOT EXISTS idx_checkpoint_concepts_ck ON checkpoint_concepts(checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_checkpoint_concepts_importance ON checkpoint_concepts(checkpoint_id, importance);
CREATE INDEX IF NOT EXISTS idx_query_log_concept      ON concept_query_log(concept_id);
CREATE INDEX IF NOT EXISTS idx_query_log_session      ON concept_query_log(session_id);
CREATE INDEX IF NOT EXISTS idx_query_log_concept_time ON concept_query_log(concept_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_exam_records_course    ON exam_records(course_id);
CREATE INDEX IF NOT EXISTS idx_retrospectives_exam    ON exam_retrospectives(exam_id);
