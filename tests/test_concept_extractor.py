"""
Tests for Phase 4A: ConceptExtractor + SQLiteStore concept CRUD.

只测试 Pass 1 (规则提取) + 持久化 + 查询。
Pass 2/3 (LLM 调用) 不在单元测试中覆盖（需要真实 API key）。
"""
from pathlib import Path
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.concept_extractor import (
    ConceptExtractor,
    ExtractedConcept,
    ExtractedRelation,
    normalize_concept_id,
)
from nexus.knowledge.parser import ParsedChunk


def _make_store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "test.db")


def _make_chunks() -> tuple[list[ParsedChunk], list[str]]:
    chunks = [
        ParsedChunk(
            topic="死锁的四个必要条件",
            content="死锁发生需要同时满足互斥、持有并等待、不可剥夺、循环等待四个条件。",
            type="theorem",
            keywords=["死锁", "deadlock", "必要条件", "互斥", "循环等待", "hold-and-wait"],
            prerequisites=["进程", "资源分配"],
            page=5,
            continues=False,
        ),
        ParsedChunk(
            topic="死锁预防策略",
            content="通过破坏四个必要条件之一来预防死锁：如资源一次性分配。",
            type="technique",
            keywords=["死锁", "deadlock", "预防", "prevention", "资源分配", "一次性分配"],
            prerequisites=["死锁的四个必要条件"],
            page=8,
            continues=False,
        ),
        ParsedChunk(
            topic="银行家算法",
            content="银行家算法是一种死锁避免算法，通过安全序列判断来决定是否分配资源。",
            type="algorithm",
            keywords=["银行家算法", "banker's algorithm", "死锁避免", "安全序列", "资源分配"],
            prerequisites=["死锁", "资源分配"],
            page=12,
            continues=False,
        ),
    ]
    chunk_ids = ["CS202_OS_Lec07_000", "CS202_OS_Lec07_001", "CS202_OS_Lec07_002"]
    return chunks, chunk_ids


# ────────────────────────────────────────────────
# normalize_concept_id
# ────────────────────────────────────────────────

def test_normalize_concept_id_english():
    assert normalize_concept_id("Deadlock Detection", "CS202_OS") == "cs202_os__deadlock_detection"


def test_normalize_concept_id_chinese():
    cid = normalize_concept_id("死锁检测", "CS202_OS")
    assert cid == "cs202_os__死锁检测"


def test_normalize_concept_id_mixed():
    cid = normalize_concept_id("死锁 / Deadlock", "CS202_OS")
    assert "cs202_os__" in cid
    assert "死锁" in cid


# ────────────────────────────────────────────────
# Pass 1: 规则提取
# ────────────────────────────────────────────────

def test_extract_from_chunks_produces_concepts(tmp_path):
    store = _make_store(tmp_path)
    extractor = ConceptExtractor(store)
    chunks, chunk_ids = _make_chunks()

    concepts = extractor.extract_from_chunks(chunks, chunk_ids, "CS202_OS")

    assert len(concepts) > 0
    names = [c.name for c in concepts]
    # Topics 应该都被提取
    assert "死锁的四个必要条件" in names
    assert "死锁预防策略" in names
    assert "银行家算法" in names


def test_extract_from_chunks_finds_frequent_keywords(tmp_path):
    store = _make_store(tmp_path)
    extractor = ConceptExtractor(store)
    chunks, chunk_ids = _make_chunks()

    concepts = extractor.extract_from_chunks(chunks, chunk_ids, "CS202_OS")
    names = [c.name for c in concepts]

    # "死锁" 出现在所有 3 个 chunks 的 keywords 中 → 应被提取
    assert "死锁" in names
    # "deadlock" 也出现 ≥2 次
    assert "deadlock" in names


def test_extract_from_chunks_finds_prerequisites(tmp_path):
    store = _make_store(tmp_path)
    extractor = ConceptExtractor(store)
    chunks, chunk_ids = _make_chunks()

    concepts = extractor.extract_from_chunks(chunks, chunk_ids, "CS202_OS")
    names = [c.name for c in concepts]

    # "进程" 和 "资源分配" 是 prerequisites
    assert "进程" in names
    assert "资源分配" in names

    by_name = {c.name: c for c in concepts}
    assert by_name["进程"].source_chunk_ids == ["CS202_OS_Lec07_000"]
    assert by_name["资源分配"].source_chunk_ids == [
        "CS202_OS_Lec07_000",
        "CS202_OS_Lec07_001",
        "CS202_OS_Lec07_002",
    ]


def test_extract_filters_generic_and_question_like_candidates(tmp_path):
    store = _make_store(tmp_path)
    extractor = ConceptExtractor(store)
    chunks = [
        ParsedChunk(
            topic="为什么要使用中断？",
            content="中断避免 CPU 持续轮询。",
            type="overview",
            keywords=["为什么", "how", "中断", "interrupt"],
            prerequisites=["什么", "轮询"],
            page=1,
            continues=False,
        ),
        ParsedChunk(
            topic="Interrupt handling",
            content="Interrupt handling saves processor state.",
            type="technique",
            keywords=["中断", "interrupt", "compare"],
            prerequisites=["轮询"],
            page=2,
            continues=False,
        ),
    ]

    concepts = extractor.extract_from_chunks(chunks, ["c1", "c2"], "OS")
    names = {c.name for c in concepts}

    assert "为什么要使用中断？" not in names
    assert not {"为什么", "how", "compare", "什么"} & names
    assert {"Interrupt handling", "中断", "interrupt", "轮询"} <= names


def test_extract_infers_concept_type(tmp_path):
    store = _make_store(tmp_path)
    extractor = ConceptExtractor(store)
    chunks, chunk_ids = _make_chunks()

    concepts = extractor.extract_from_chunks(chunks, chunk_ids, "CS202_OS")
    by_name = {c.name: c for c in concepts}

    assert by_name["死锁的四个必要条件"].concept_type == "theorem"
    assert by_name["死锁预防策略"].concept_type == "technique"
    assert by_name["银行家算法"].concept_type == "technique"


# ────────────────────────────────────────────────
# 持久化
# ────────────────────────────────────────────────

def test_persist_writes_to_sqlite(tmp_path):
    store = _make_store(tmp_path)
    extractor = ConceptExtractor(store)
    chunks, chunk_ids = _make_chunks()

    # 先写入 chunks 到 SQLite（persist 需要 chunk_concepts FK）
    store.upsert_document("CS202_OS/Lec07.pdf", "CS202_OS", "Lec07.pdf", "abc123")
    for i, chunk in enumerate(chunks):
        store.upsert_chunk(
            chunk_id=chunk_ids[i],
            document_id="CS202_OS/Lec07.pdf",
            course_id="CS202_OS",
            topic=chunk.topic,
        )

    concepts = extractor.extract_from_chunks(chunks, chunk_ids, "CS202_OS")
    stats = extractor.persist(concepts, [])

    assert stats["concepts"] > 0

    # 验证 SQLite 有数据
    db_concepts = store.get_concepts_by_course("CS202_OS")
    assert len(db_concepts) > 0

    # 验证 chunk_concepts 关联
    linked = store.get_concepts_for_chunks(chunk_ids)
    assert len(linked) > 0


def test_persist_writes_relations(tmp_path):
    store = _make_store(tmp_path)
    extractor = ConceptExtractor(store)

    concepts = [
        ExtractedConcept("cs202_os__deadlock", "Deadlock", "CS202_OS", source_chunk_ids=[]),
        ExtractedConcept("cs202_os__prevention", "Prevention", "CS202_OS", source_chunk_ids=[]),
    ]
    relations = [
        ExtractedRelation(
            source_id="cs202_os__prevention",
            target_id="cs202_os__deadlock",
            relation_type="prerequisite",
            confidence=0.9,
            evidence="Prevention requires understanding deadlock first",
        ),
    ]

    stats = extractor.persist(concepts, relations)
    assert stats["concepts"] == 2
    assert stats["relations"] == 1

    # 验证关系可查询
    rels = store.get_relations_for_concept("cs202_os__deadlock")
    assert len(rels) == 1
    assert rels[0]["relation_type"] == "prerequisite"


# ────────────────────────────────────────────────
# SQLiteStore concept CRUD
# ────────────────────────────────────────────────

def test_upsert_concept_idempotent(tmp_path):
    store = _make_store(tmp_path)
    store.upsert_concept("test__a", "Concept A", "TEST", definition="First def")
    store.upsert_concept("test__a", "Concept A updated", "TEST", definition="Updated def")

    c = store.get_concept("test__a")
    assert c is not None
    assert c["name"] == "Concept A updated"
    assert c["definition"] == "Updated def"


def test_get_concepts_by_course(tmp_path):
    store = _make_store(tmp_path)
    store.upsert_concept("os__a", "A", "OS")
    store.upsert_concept("os__b", "B", "OS")
    store.upsert_concept("db__c", "C", "DB")

    os_concepts = store.get_concepts_by_course("OS")
    assert len(os_concepts) == 2
    db_concepts = store.get_concepts_by_course("DB")
    assert len(db_concepts) == 1


def test_cross_course_relations(tmp_path):
    store = _make_store(tmp_path)
    store.upsert_concept("os__mutex", "Mutex", "OS")
    store.upsert_concept("db__txn_iso", "Transaction Isolation", "DB")
    store.upsert_relation(
        "os__mutex", "db__txn_iso", "analogous_to", 0.9,
        "Both use exclusive access to prevent concurrent conflicts",
    )

    cross = store.get_cross_course_relations("os__mutex")
    assert len(cross) == 1
    assert cross[0]["relation_type"] == "analogous_to"
    assert cross[0]["target_name"] == "Transaction Isolation"
