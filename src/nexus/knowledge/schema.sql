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
