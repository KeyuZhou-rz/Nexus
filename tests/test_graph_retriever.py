"""
Tests for Phase 4A: GraphRetriever.

验证从 chunk_ids 出发的图谱遍历能正确返回:
- query_concepts (直接关联概念)
- prerequisites (前置知识)
- related_same_course (同课程相关)
- related_cross_course (跨课程关联)
"""
from pathlib import Path
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.graph_retriever import GraphRetriever


def _setup_graph(tmp_path: Path) -> tuple[SQLiteStore, list[str]]:
    """搭建一个小型测试图谱。"""
    store = SQLiteStore(tmp_path / "test.db")

    # 创建文档和 chunks
    store.upsert_document("CS202_OS/Lec07.pdf", "CS202_OS", "Lec07.pdf", "abc")
    chunk_ids = ["CS202_OS_Lec07_000", "CS202_OS_Lec07_001"]
    for cid in chunk_ids:
        store.upsert_chunk(cid, "CS202_OS/Lec07.pdf", "CS202_OS", topic="test")

    # 创建概念
    store.upsert_concept("os__deadlock", "死锁 / Deadlock", "CS202_OS",
                         definition="两个或多个进程互相等待对方持有的资源")
    store.upsert_concept("os__mutex", "互斥锁 / Mutex", "CS202_OS",
                         definition="保证同一时刻只有一个线程访问临界区")
    store.upsert_concept("os__process", "进程 / Process", "CS202_OS",
                         definition="程序的执行实例")
    store.upsert_concept("db__txn_iso", "事务隔离 / Transaction Isolation", "DB",
                         definition="确保并发事务互不干扰")

    # chunk ↔ concept 关联
    store.link_chunk_to_concept("CS202_OS_Lec07_000", "os__deadlock")
    store.link_chunk_to_concept("CS202_OS_Lec07_001", "os__mutex")

    # 课内关系
    store.upsert_relation("os__deadlock", "os__process", "prerequisite", 0.9,
                          "理解死锁需要先理解进程")
    store.upsert_relation("os__deadlock", "os__mutex", "extends", 0.7,
                          "死锁是互斥锁使用不当的结果")

    # 跨课程关系
    store.upsert_relation("os__mutex", "db__txn_iso", "analogous_to", 0.85,
                          "都通过排他性访问防止并发冲突")

    return store, chunk_ids


def test_retrieve_finds_query_concepts(tmp_path):
    store, chunk_ids = _setup_graph(tmp_path)
    retriever = GraphRetriever(store)

    ctx = retriever.retrieve(chunk_ids, course_id="CS202_OS")

    concept_names = [c["name"] for c in ctx.query_concepts]
    assert "死锁 / Deadlock" in concept_names
    assert "互斥锁 / Mutex" in concept_names


def test_retrieve_finds_prerequisites(tmp_path):
    store, chunk_ids = _setup_graph(tmp_path)
    retriever = GraphRetriever(store)

    ctx = retriever.retrieve(chunk_ids, course_id="CS202_OS")

    prereq_names = [p.name for p in ctx.prerequisites]
    assert "进程 / Process" in prereq_names


def test_retrieve_finds_cross_course(tmp_path):
    store, chunk_ids = _setup_graph(tmp_path)
    retriever = GraphRetriever(store)

    ctx = retriever.retrieve(chunk_ids, course_id="CS202_OS")

    cross_names = [c.name for c in ctx.related_cross_course]
    assert "事务隔离 / Transaction Isolation" in cross_names
    assert ctx.related_cross_course[0].is_cross_course is True
    assert ctx.related_cross_course[0].relation_type == "analogous_to"


def test_retrieve_empty_when_no_concepts(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.upsert_document("X/f.pdf", "X", "f.pdf", "h")
    store.upsert_chunk("X_f_000", "X/f.pdf", "X", topic="t")

    retriever = GraphRetriever(store)
    ctx = retriever.retrieve(["X_f_000"])

    assert not ctx.has_content
    assert ctx.query_concepts == []


def test_retrieve_has_content(tmp_path):
    store, chunk_ids = _setup_graph(tmp_path)
    retriever = GraphRetriever(store)

    ctx = retriever.retrieve(chunk_ids, course_id="CS202_OS")
    assert ctx.has_content is True
