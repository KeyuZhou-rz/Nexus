"""
Phase 3.5 Day 3-4: 检索质量测试
按 phase3.5DEVguide.md 条目评估：
  - 检索到的 chunks 是否相关？
  - 课程推断是否正确？
  - Query Expansion 改写是否有帮助？
  - 最终回答是否基于课件而非通用知识？
"""
import os, sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nexus.knowledge.qa_pipeline import QAPipeline

QWEN_KEY = json.loads(Path("data/QWEN_API_KEY.json").read_text())["QWEN_API_KEY"]
os.environ["QWEN_API_KEY"] = QWEN_KEY

pipeline = QAPipeline(
    chroma_dir=Path("data/chroma"),
    sqlite_path=Path("data/nexus.db"),
    tasks_path=Path("data/tasks.json"),
    qwen_api_key=QWEN_KEY,
)
pipeline.add_course("CS202_OS")

SESSION = "test_3_5"

# 12 个真实 OS 问题：定义类、应用类、对比类、作业题类
QUESTIONS = [
    # 定义类
    ("Q01", "什么是进程？进程和程序有什么区别？"),
    ("Q02", "Trap 和 Interrupt 的定义是什么？它们有什么区别？"),
    ("Q03", "什么是系统调用？用户程序如何通过 trap 进入内核？"),
    # 应用类
    ("Q04", "fork() 调用之后，父进程和子进程分别会发生什么？"),
    ("Q05", "上下文切换（context switch）的步骤是什么？"),
    ("Q06", "用户态和内核态的切换是如何触发的？"),
    # 对比类
    ("Q07", "轮询（polling）和中断（interrupt）在 I/O 处理上有什么优劣？"),
    ("Q08", "进程和线程的区别是什么？"),
    # 作业题类
    ("Q09", "如果一个程序调用了非法内存地址，操作系统会如何响应？"),
    ("Q10", "请解释 trap table 的作用，系统启动时它是如何初始化的？"),
    ("Q11", "在 limited direct execution 模型中，OS 是如何重新获取 CPU 控制权的？"),
    ("Q12", "exec() 和 fork() 通常配合使用，请描述 shell 执行一条命令的完整流程。"),
]

PASS_MARK = "✓"
FAIL_MARK = "✗"
WARN_MARK = "⚠"

results = []

for qid, question in QUESTIONS:
    print(f"\n{'='*60}")
    print(f"[{qid}] {question}")
    print("─" * 60)

    resp = pipeline.ask(question, session_id=SESSION, course_id="CS202_OS")

    # 展示 query expansion
    print(f"扩展检索词: {resp.expanded_queries}")

    # 展示 chunks
    if resp.sources:
        print(f"检索到 {len(resp.sources)} 个 chunks:")
        for src in resp.sources:
            print(f"  [{src.score:.3f}] {src.topic} — {src.source_file}")
    else:
        print(f"{WARN_MARK} 未检索到任何 chunk（将基于通用知识回答）")

    # 展示回答（前 300 字）
    answer_preview = resp.answer[:300].replace("\n", " ")
    print(f"\n回答: {answer_preview}{'...' if len(resp.answer) > 300 else ''}")

    # 评估
    has_chunks = len(resp.sources) > 0
    has_expansion = len(resp.expanded_queries) > 1
    course_ok = resp.course_id == "CS202_OS"

    status = PASS_MARK if has_chunks else FAIL_MARK
    print(f"\n{status} chunks={len(resp.sources)} | "
          f"expanded={has_expansion} | "
          f"course={resp.course_id} | "
          f"model={resp.model_used}")

    results.append({
        "id": qid,
        "question": question,
        "chunks": len(resp.sources),
        "chunk_topics": [s.topic for s in resp.sources],
        "expanded_queries": resp.expanded_queries,
        "course_ok": course_ok,
        "has_chunks": has_chunks,
        "warnings": resp.warnings,
    })

# ── 汇总 ──
print(f"\n{'='*60}")
print("检索质量汇总")
print("─" * 60)
ok = sum(1 for r in results if r["has_chunks"])
print(f"有效检索: {ok}/{len(results)} 个问题")
print(f"平均 chunks 数: {sum(r['chunks'] for r in results)/len(results):.1f}")
print()

no_chunk = [r for r in results if not r["has_chunks"]]
if no_chunk:
    print(f"{FAIL_MARK} 未检索到内容的问题（需要调整）:")
    for r in no_chunk:
        print(f"  [{r['id']}] {r['question']}")
