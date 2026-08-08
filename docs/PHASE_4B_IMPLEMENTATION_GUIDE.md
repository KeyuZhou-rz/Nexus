# Phase 4B Implementation Guide — 索引与历史数据治理

> 执行顺序：4B-1 → 4B-2 → 4B-3 → 4B-4 → 4B-5，每步单独提交。
> 提交前：定向测试 → 全量测试 → CLI smoke → db-check。
> 数据库、Chroma、凭据和 pycache 不进入提交。

---

## 当前数据基线

```
documents:  5 行, 全部 MD5 (32-char hex)
chunks:     70 行, SQLite 无 chunk_index 列
concepts:   324 行, 141 孤儿 (无 chunk_concepts 链接), 294 无 definition
chroma:     70 向量, 仅 1 个有效 collection (CS202_OS)
           Chroma metadata 缺少: schema_version, document_id, chunk_id,
           file_hash, chunk_index
```

---

## 4B-1：只读索引审计

**目标**：扩展 `KnowledgeConsistencyAudit`，每次 db-check 能输出所有待修复项的精确数量和分布。

### 涉及文件

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/nexus/knowledge/chunk_backfill.py` | 修改 | 扩展 `KnowledgeConsistencyAudit` |
| `src/nexus/cli/db_check.py` | 修改 | CLI 输出新审计字段 |

### 具体改动

#### 1.1 扩展 `KnowledgeConsistencyAudit.run()` (chunk_backfill.py:59-115)

当前 `run()` 检查 5 项：
- `chroma_chunk_missing_sqlite` — Chroma 有但 SQLite 无
- `course_id_mismatch` — 两边的 course_id 不一致
- `missing_source_file` — Chroma metadata 缺 source_file
- `chroma_document_missing_sqlite` — document 不在 SQLite
- `sqlite_chunk_missing_chroma` — SQLite 有但 Chroma 无
- `document_chunk_count_mismatch` — chunk_count 不匹配

**新增检查项**（每个都是一个新的 `ConsistencyIssue` code）：

```python
# 1. Chroma metadata 完整性 — 逐字段报告缺失率
MISSING_META_FIELDS = [
    "schema_version", "document_id", "chunk_id",
    "file_hash", "chunk_index"
]
# 对每个 Chroma chunk 的 metadata，检查上述字段是否存在（key missing 或值为空/0/None）
# issue code: "chroma_metadata_missing_field"
# message 示例: "70/70 chunks missing field 'schema_version'"

# 2. Collection 级别元数据 — 检查 collection metadata 中是否有 course_id
# issue code: "collection_missing_course_metadata"

# 3. SQLite chunks.chunk_index 列是否存在
# PRAGMA table_info('chunks') 检查列名
# issue code: "sqlite_chunks_missing_chunk_index_column"

# 4. 文档 file_hash 长度审计 — 区分 MD5 (32) vs SHA-256 (64)
# SELECT id, length(file_hash) as hl FROM documents WHERE hl != 64
# issue code: "document_non_sha256_hash"

# 5. 分页完整性 — Chroma collection.get() 分批拉取，检查总数是否匹配
# 如果 collection.count() != len(ids)，issue code: "chroma_pagination_gap"

# 6. Collection ↔ course 一致性
# collection.name 与 collection metadata 中的 course_id 是否一致
# issue code: "collection_course_id_mismatch"
```

**实现方式**：在 `run()` 的现有循环中插入这些检查，保持向后兼容的返回格式。返回的 `issues` 列表中新增这些 code。

#### 1.2 更新 CLI 输出 (cli/db_check.py)

`nexus db-check` 调用 `KnowledgeConsistencyAudit.run()` 后打印结果。确保新 issue code 在输出中清晰可读。

**验证**：
```bash
# 审计应该报告：
# - 70 chunks missing schema_version/document_id/chunk_id/file_hash/chunk_index
# - 5 documents with MD5 hash (non-SHA-256)
# - 0 sqlite chunks missing chunk_index column (先有列才能审计值)
# - 141 orphan concepts
PYTHONPATH=src python -m nexus.cli.db_check
```

---

## 4B-2：Metadata/Hash 迁移

**目标**：将 70 个旧 Chroma 向量和 5 个旧 SQLite 文档升级到新元数据契约。

### 涉及文件

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/nexus/knowledge/schema.sql` | 修改 | `chunks` 表加 `chunk_index` 列（如缺失）；修正 `documents.file_hash` 注释 |
| `src/nexus/knowledge/chunk_backfill.py` | 修改 | 新增 `MetadataMigration` 类 |
| `src/nexus/cli/db_repair.py` | 修改 | 增加 `--migrate-metadata` 子命令，调用 `MetadataMigration` |
| `src/nexus/knowledge/ingestor.py` | 修改 | 第 198 行注释："计算文件 MD5" → "计算文件 SHA-256" |

### 具体改动

#### 2.1 修正 `documents.file_hash` 注释 (schema.sql:19)

```sql
-- 当前（错误）:
file_hash TEXT NOT NULL,  -- 文件 MD5，用于检测文件是否更新

-- 改为:
file_hash TEXT NOT NULL,  -- 文件 SHA-256，用于检测文件是否更新
```

同时确认 `chunk_index` 列是否需要添加。当前数据中该列不存在，需通过 migration 添加：

```sql
-- 仅当列不存在时执行
ALTER TABLE chunks ADD COLUMN chunk_index INTEGER;
```

#### 2.2 新增 `MetadataMigration` 类 (chunk_backfill.py)

在 `ChunkBackfill` 类之后新增一个 `MetadataMigration` 类：

```python
class MetadataMigration:
    """
    将旧 ChromaDB 向量升级到新元数据契约。

    操作（幂等，可重复执行）：
    1. 对每个 Chroma chunk，补全 metadata 字段：
       - schema_version → "1"
       - document_id   → f"{course_id}/{source_file}"
       - chunk_id      → 从 Chroma id 推导或按规则生成
       - file_hash     → 从 SQLite documents 表查找；找不到则标记 "unknown"
       - chunk_index   → 从 Chroma id 推导（如 "CS202_OS_04-processes_000" → 0）
    2. 对每个 SQLite documents 行，如果 file_hash 是 32 字符（MD5），
       重新计算文件的 SHA-256 并更新。
    3. 对每个 SQLite chunks 行，填充 chunk_index（从 Chroma id 解析）。

    所有操作先 dry-run（默认），输出待修改项；--apply 后才写入。
    """

    def __init__(self, sqlite_path: Path, chroma_path: Path):
        ...

    def plan(self) -> dict:
        """dry-run: 返回将要修改的项，不写入任何数据。
        返回: {
            "chroma_metadata_updates": 70,  # 需要补 metadata 的向量数
            "document_hash_updates": 5,      # MD5→SHA-256 的文档数
            "sqlite_chunk_index_updates": 70 # 需要填充 chunk_index 的行数
        }
        """

    def apply(self) -> dict:
        """执行迁移，返回实际修改数。先自动调用 db_backup。"""
```

**Chroma chunk_index 推导逻辑**（从现有 ID 格式）：
```
"CS202_OS_04-processes_000" → chunk_index = 0
"CS202_OS_05-traps_000"    → chunk_index = 0
"CS202_OS_05-traps_001"    → chunk_index = 1
```
规则：取 ID 最后一个 `_` 后的数字部分。

**Document SHA-256 重算逻辑**：
```
对于每个 file_hash 长度为 32 的文档：
1. 在 data/archives/ 或 data/testDataFile/ 下查找对应文件
2. 如果找到，用 file_content_hash() 计算 SHA-256，更新 documents.file_hash
3. 如果找不到，标记为 "file_missing"，file_hash 设为 "unknown_<原MD5值>"
```

#### 2.3 更新 `nexus db-repair` CLI (cli/db_repair.py)

当前 `db-repair` 只修复 document chunk_count drift。新增子命令：

```bash
# dry-run 模式（默认）
PYTHONPATH=src python -m nexus.cli.db_repair --migrate-metadata

# 实际执行（先自动备份）
PYTHONPATH=src python -m nexus.cli.db_repair --migrate-metadata --apply
```

#### 2.4 修正 ingestor.py 注释 (ingestor.py:198)

```python
# 当前:
# 1. 计算文件 MD5

# 改为:
# 1. 计算文件 SHA-256
```

### 迁移步骤

```bash
# 1. 备份
cp data/nexus.db data/nexus.db.4b2.bak

# 2. dry-run 看计划
PYTHONPATH=src python -m nexus.cli.db_repair --migrate-metadata

# 3. 执行
PYTHONPATH=src python -m nexus.cli.db_repair --migrate-metadata --apply

# 4. 验证
PYTHONPATH=src python -m nexus.cli.db_check
PYTHONPATH=src python -m pytest tests/ -q
```

---

## 4B-3：Concept Quarantine/Cleanup

**目标**：识别并隔离 141 个孤立 concept + 22 个 Berkshire/投资概念 + 4 个伪概念。

### 涉及文件

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/nexus/knowledge/chunk_backfill.py` | 修改 | 新增 `ConceptQuarantine` 类 |
| `src/nexus/cli/db_repair.py` | 修改 | 增加 `--quarantine-concepts` 子命令 |

### 具体改动

#### 3.1 新增 `ConceptQuarantine` 类 (chunk_backfill.py)

```python
class ConceptQuarantine:
    """
    识别和隔离伪概念/孤立概念。

    分类规则（按优先级）：
    A. 伪概念 — 确定性规则，不需要 chunk 证据：
       - name 是纯问词: "如何", "为什么", "区别", "什么" (name 长度 ≤ 3 且全是 CJK 疑问词)
       - name 匹配投资/伯克希尔关键词: Berkshire, 巴菲特, 芒格, 伯克希尔, 投资(非"投资回报率"类CS术语)
       - name 匹配 "第X章", "第X节" 等大纲标题模式
       - name 是纯标点或纯数字
    B. 孤立概念 — concept 无 chunk_concepts 行
    C. 无定义概念 — definition IS NULL 且 type IS NULL（LLM 从未验证过）

    默认 dry-run，输出分类计数和受影响 concept_id 列表。
    --apply 后：
    - 伪概念：DELETE FROM concepts CASCADE (删除 concept + relations + aliases)
    - 孤立+无定义：移入 concept_quarantine 表（软删除，可恢复）
    - 孤立+有定义：保留，标记 concept_type='orphan'
    """

    def plan(self) -> dict:
        """返回: {
            "pseudo_concepts": [
                {"concept_id": "cs202_os__如何", "name": "如何", "reason": "question_word"},
                ...
            ],
            "orphan_no_definition": [concept_id, ...],  # 100 个
            "orphan_with_definition": [concept_id, ...], # 41 个
        }"""

    def apply(self, categories: list[str] = ["pseudo", "orphan_no_def"]) -> dict:
        """执行隔离。categories 控制哪些分类被处理。"""
```

#### 3.2 schema.sql 加 quarantine 表

```sql
-- 被隔离的概念（软删除，可恢复）
CREATE TABLE IF NOT EXISTS concept_quarantine (
    concept_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    course_id       TEXT NOT NULL,
    definition      TEXT,
    concept_type    TEXT,
    quarantine_reason TEXT NOT NULL,  -- "pseudo_concept" | "orphan_no_definition"
    original_data   TEXT,             -- JSON: 原 concepts 行 + relations + aliases
    quarantined_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 3.3 更新 db_repair CLI

```bash
# dry-run: 列出所有待清理项
PYTHONPATH=src python -m nexus.cli.db_repair --quarantine-concepts

# 执行隔离
PYTHONPATH=src python -m nexus.cli.db_repair --quarantine-concepts --apply

# 仅隔离伪概念，保留无定义孤儿
PYTHONPATH=src python -m nexus.cli.db_repair --quarantine-concepts --apply --categories=pseudo

# 恢复被隔离的概念
PYTHONPATH=src python -m nexus.cli.db_repair --quarantine-concepts --restore=cs202_os__如何
```

### 伪概念判断规则（确定性，不调 LLM）

```
伪概念关键词列表:
  INVESTMENT_KEYWORDS = [
    "Berkshire", "berkshire", "伯克希尔", "巴菲特", "芒格",
    "投资", "股东", "股东大会", "慈善捐赠"
  ]
  QUESTION_WORDS = {"如何", "为什么", "区别", "什么", "怎么", "哪里", "何时"}
  OUTLINE_PATTERNS = [r"^第[一二三四五六七八九十\d]+章", r"^第[一二三四五六七八九十\d]+节"]
```

**预期清理结果**：
```
伪概念 (~26 个):    DELETE CASCADE
  22 Berkshire/投资 + 4 纯问词
孤立+无定义 (~100个): 移入 concept_quarantine
孤立+有定义 (~41个):  标记 type='orphan', 保留
─────────────────────────────────
剩余 concepts: 324 - 26 - 100 = ~198 (其中 41 个标记 orphan)
```

---

## 4B-4：Chroma 版本兼容

**目标**：固定经过验证的版本范围，防止 pip 安装时解析到不兼容版本。

### 涉及文件

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `pyproject.toml` | 修改 | 收紧 chromadb 版本约束 |
| `src/nexus/doctor.py` | 修改 | 检查 ChromaDB 版本是否在支持范围内 |

### 具体改动

#### 4.1 固定 ChromaDB 版本 (pyproject.toml)

```toml
# 当前（第 19 行）:
"chromadb>=0.5",

# 改为:
"chromadb>=0.5,<0.6 | >=1.0,<2.0",
```

**为什么这样约束**：
- `0.5.x` — 项目最初使用的版本，数据格式兼容
- `0.6.x` — **不兼容**：`list_collections()` API 变化，无法读取旧数据（`KeyError('_type')`）
- `1.0.x - 1.x` — 已验证可用（当前 dev 环境 1.5.9，542 tests pass）

如果用 pip 的 `!=` 语法更清晰：

```toml
"chromadb>=0.5,!=0.6.*,!=0.7.*,!=0.8.*,!=0.9.*",
```

但 `!=` 对预发布版本可能有问题。推荐使用范围约束或下限：

```toml
# 最简单：只允许已测试的版本
"chromadb>=1.0",
```

> **决策点**：确认 CI 矩阵中 0.5.x (Ubuntu) 和 1.x 都能通过测试后，再决定是 `>=1.0` 还是保留 `>=0.5` 双区间。

#### 4.2 Doctor 版本检查 (doctor.py)

在 `_check_chroma()` 中添加版本检查：

```python
# 在 try 块内，chromadb import 成功后:
import chromadb
chroma_version = tuple(int(x) for x in chromadb.__version__.split(".")[:2])
comp.details["chromadb_version"] = chromadb.__version__
if chroma_version < (1, 0):
    comp.warnings.append(
        f"ChromaDB {chromadb.__version__} is below recommended 1.0; "
        "upgrade with: pip install 'chromadb>=1.0'"
    )
```

#### 4.3 内部 foreign key 检查

ChromaDB 没有 SQL 那样的 foreign key。这里指：
- 每个 Chroma chunk 的 `document_id` 应对应 SQLite `documents` 表中的一行
- 每个 Chroma chunk 的 `chunk_id` 应在 SQLite `chunks` 表中有对应行

这些检查已在 4B-1 中通过 `KnowledgeConsistencyAudit` 覆盖（`chroma_chunk_missing_sqlite`、`chroma_document_missing_sqlite`）。

---

## 4B-5：迁移安全保障

**目标**：每次 apply 前自动备份，迁移后自动校验。

### 涉及文件

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/nexus/knowledge/maintenance.py` | 参考 | 已有 `create_backup()`/`restore_backup()`，不改 |
| `src/nexus/cli/db_repair.py` | 修改 | apply 前自动调 backup |
| `src/nexus/knowledge/chunk_backfill.py` | 修改 | `MetadataMigration.apply()` 和 `ConceptQuarantine.apply()` 内置备份钩子 |

### 具体改动

#### 5.1 Migration 基类 (chunk_backfill.py)

新增一个 `SafeMigration` 基类或 mixin：

```python
class SafeMigration:
    """所有迁移操作的基类，提供自动备份 + 迁移后校验。"""

    def __init__(self, sqlite_path: Path, chroma_path: Path, data_dir: Path):
        self.sqlite_path = sqlite_path
        self.chroma_path = chroma_path
        self.data_dir = data_dir

    def pre_migration_backup(self) -> Path:
        """创建迁移前备份，返回备份路径。"""
        from nexus.knowledge.maintenance import create_backup
        return create_backup(self.data_dir)  # 内部调 sqlite backup + Chroma copy

    def post_migration_verify(self) -> bool:
        """迁移后运行 db-check + retrieval golden set。"""
        # 运行 KnowledgeConsistencyAudit，验证 0 issues
        auditor = KnowledgeConsistencyAudit(self.sqlite_path, self.chroma_path)
        result = auditor.run()
        if not result["ok"]:
            logger.error("Post-migration audit failed: %d issues", len(result["issues"]))
            return False
        return True
```

#### 5.2 CLI db-repair --apply 流程

```bash
nexus db-repair --migrate-metadata --apply
```

内部执行顺序：
1. `pre_migration_backup()` → `data/backups/nexus_backup_<timestamp>.zip`
2. `MetadataMigration.apply()`
3. `post_migration_verify()` → 调 `KnowledgeConsistencyAudit.run()`
4. 如果 verify 失败 → 打印 "Run: nexus db-restore <backup_path>"
5. 打印 dry-run 结果 vs 实际结果对比

---

## 提交拆分

每个子项一个 commit，顺序如下：

```
1. [4B-1] feat(audit): extend KnowledgeConsistencyAudit with metadata completeness checks
   - chunk_backfill.py: 新增 6 个 issue code
   - db_check.py: 更新 CLI 输出

2. [4B-2a] fix(schema): add chunk_index column, correct file_hash docstring
   - schema.sql: ALTER TABLE + 注释修正
   - ingestor.py: 修正注释

3. [4B-2b] feat(migration): MetadataMigration for Chroma metadata + SQLite hash migration
   - chunk_backfill.py: 新增 MetadataMigration 类
   - db_repair.py: 新增 --migrate-metadata 子命令

4. [4B-3a] feat(quarantine): ConceptQuarantine for pseudo/orphan concept detection
   - chunk_backfill.py: 新增 ConceptQuarantine 类
   - schema.sql: 新增 concept_quarantine 表

5. [4B-3b] feat(quarantine): --quarantine-concepts CLI with dry-run/apply/restore
   - db_repair.py: 新增 --quarantine-concepts 子命令

6. [4B-4] fix(deps): pin chromadb version range, add version check to doctor
   - pyproject.toml: 收紧版本约束
   - doctor.py: 新增版本检查

7. [4B-5] feat(migration): SafeMigration base class with pre-backup + post-verify
   - chunk_backfill.py: 新增 SafeMigration 基类
   - db_repair.py: apply 流程集成
```

---

## 每步验收命令

```bash
# 提交后必跑：
PYTHONPATH=src python -m pytest tests/ -q        # 542 passed
PYTHONPATH=src python -m nexus.cli.doctor         # 检查无新增 warnings
PYTHONPATH=src python -m nexus.cli.db-check       # 检查审计输出
PYTHONPATH=src python -m nexus.cli.main --help    # CLI smoke
```

---

## 风险点与注意事项

1. **ChromaDB 版本切换**：如果 CI 安装了 `chromadb>=1.0`，现有的 `data/chroma/` 目录需要重新 ingest 或先迁移。CI 测试使用 mock ChromaDB（OfflineClient），不受影响。

2. **Document SHA-256 重算**：如果原始文件已不在磁盘上（被移动/删除），无法重算 hash。此时 `file_hash` 保持 MD5 值但前缀加 `md5:` 或 `unknown_` 标记。

3. **Concept 删除不可逆**：伪概念的 DELETE CASCADE 是硬删除。quarantine 表存储可恢复的数据。建议在 4B-3 apply 前人工审查 dry-run 输出。

4. **Chroma metadata 更新**：`collection.update(ids=..., metadatas=...)` 是原地更新，不需要重新 embedding。但需要先 `collection.get()` 拿到完整 metadata dict，合并新字段后再 `update()`，避免覆盖已有字段。

5. **并发**：迁移期间应该获取 `maintenance_lock`，阻止其他进程同时写入。
