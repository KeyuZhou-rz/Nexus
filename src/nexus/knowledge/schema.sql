-- Nexus Phase 3: SQLite Schema
-- 所有表均支持幂等创建（IF NOT EXISTS）
-- WAL 模式和外键约束须在每次连接时手动启用（见 sqlite_store.py）

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
    parse_status    TEXT DEFAULT 'pending' -- pending | parsing | done | failed
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
-- 索引：加速常用查询路径
-- ────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_chunks_course    ON chunks(course_id);
CREATE INDEX IF NOT EXISTS idx_chunks_lecture   ON chunks(course_id, lecture_number);
CREATE INDEX IF NOT EXISTS idx_chunks_type      ON chunks(type);
CREATE INDEX IF NOT EXISTS idx_logs_session     ON interaction_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_logs_course      ON interaction_logs(course_id);

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

-- Phase 4 索引
CREATE INDEX IF NOT EXISTS idx_aliases_course         ON concept_aliases(course_id);
CREATE INDEX IF NOT EXISTS idx_aliases_concept        ON concept_aliases(concept_id);
CREATE INDEX IF NOT EXISTS idx_concepts_course          ON concepts(course_id);
CREATE INDEX IF NOT EXISTS idx_relations_source         ON concept_relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_target         ON concept_relations(target_id);
CREATE INDEX IF NOT EXISTS idx_chunk_concepts_concept   ON chunk_concepts(concept_id);
CREATE INDEX IF NOT EXISTS idx_learning_events_concept  ON learning_events(concept_id);
CREATE INDEX IF NOT EXISTS idx_learning_events_time     ON learning_events(timestamp);
