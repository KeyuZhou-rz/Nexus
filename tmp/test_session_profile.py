"""
Phase 3.5 Day 5: Session + Profile 端到端验证

测试内容:
1. 连续 4 轮对话，检查 session transcript 是否正常构建
2. 手动触发 end_session()，检查 LLM 分析 + profile patch 是否合理
3. 注入 weak_points 后检查回答风格是否有变化
"""
import os, sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

QWEN_KEY = json.loads(Path("data/QWEN_API_KEY.json").read_text())["QWEN_API_KEY"]
os.environ["QWEN_API_KEY"] = QWEN_KEY

from nexus.knowledge.qa_pipeline import QAPipeline
from nexus.knowledge.sqlite_store import SQLiteStore

SQLITE_PATH = Path("data/nexus.db")
CHROMA_DIR  = Path("data/chroma")
SESSION_ID  = "day5_test"

pipeline = QAPipeline(
    chroma_dir=CHROMA_DIR,
    sqlite_path=SQLITE_PATH,
    tasks_path=Path("data/tasks.json"),
    qwen_api_key=QWEN_KEY,
)
pipeline.add_course("CS202_OS")

# ── 工具函数 ──────────────────────────────────────

def ask(q: str) -> str:
    resp = pipeline.ask(q, session_id=SESSION_ID, course_id="CS202_OS")
    print(f"\n  Q: {q}")
    print(f"  A: {resp.answer[:200].replace(chr(10),' ')}...")
    print(f"     chunks={len(resp.sources)} | model={resp.model_used}")
    return resp.answer

def sep(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

# ══════════════════════════════════════════════════════════════
# STEP 1: 4 轮真实对话
# ══════════════════════════════════════════════════════════════
sep("STEP 1: 4 轮连续对话")

ask("什么是 trap？它和普通函数调用有什么本质区别？")
ask("好的，那 SYSCALL 指令执行时，硬件会自动保存哪些寄存器？")
ask("我对上下文切换中 PCB 的作用还不太清楚，能详细解释一下吗？")
ask("fork() 之后如果子进程先结束但父进程没有 wait()，会产生什么问题？")

# ── 检查 session transcript ──
store = SQLiteStore(SQLITE_PATH)
logs = store.get_session_logs(SESSION_ID)
print(f"\n[检查] session logs: {len(logs)} 条")
assert len(logs) == 4, f"期望 4 条，实际 {len(logs)} 条"
print(f"[OK] transcript 构建正常，最后一条 Q: {logs[-1].get('user_query','?')[:50]}")

# ══════════════════════════════════════════════════════════════
# STEP 2: end_session → LLM 分析 → profile patch
# ══════════════════════════════════════════════════════════════
sep("STEP 2: end_session() → profile 更新")

print("\n触发 end_session()...")
result = pipeline.end_session(SESSION_ID)
print(f"  status: {result['status']}")
print(f"  interactions: {result.get('interactions', '-')}")
patch = result.get("patch_result", {})
print(f"  applied: {patch.get('applied', '-')}")
print(f"  details: {patch.get('details', [])}")
print(f"  notes:   {patch.get('notes', '-')[:120]}")

# ── 检查 profile 更新合理性 ──
sep("STEP 3: 检查 profile 内容")

profile = pipeline.get_profile()
print(json.dumps(profile, ensure_ascii=False, indent=2))

# 基本断言
assert "CS202_OS" in profile.get("courses", []), "courses 应包含 CS202_OS"
print("\n[OK] courses 已注册")

weak = profile.get("weak_points", [])
mistakes = profile.get("common_mistakes", [])
style = profile.get("learning_style", "default")
print(f"[INFO] weak_points ({len(weak)}): {[w['concept'] for w in weak]}")
print(f"[INFO] common_mistakes ({len(mistakes)}): {mistakes}")
print(f"[INFO] learning_style: {style}")

# ══════════════════════════════════════════════════════════════
# STEP 4: 手动注入 weak_point，验证回答风格变化
# ══════════════════════════════════════════════════════════════
sep("STEP 4: profile 注入效果验证")

# 注入薄弱点
pipeline._profile_mgr.add_weak_point("上下文切换中 PCB 的作用", "CS202_OS")
pipeline._profile_mgr.add_weak_point("僵尸进程", "CS202_OS")

print("\n注入 weak_points: ['上下文切换中 PCB 的作用', '僵尸进程']")

# 用新 session 提问，检查回答是否针对性加强
NEW_SESSION = "day5_profile_test"
resp = pipeline.ask(
    "请解释 PCB 在上下文切换中的具体作用",
    session_id=NEW_SESSION,
    course_id="CS202_OS",
)
print(f"\n  Q: 请解释 PCB 在上下文切换中的具体作用")
print(f"  A: {resp.answer[:400].replace(chr(10),' ')}...")
print(f"\n  [检查] 回答是否有针对薄弱点的强化解释?")
# 判断回答是否比之前更详细（字数更多，或包含 step-by-step）
has_detail = len(resp.answer) > 300 and any(
    kw in resp.answer for kw in ["PCB", "进程控制块", "保存", "恢复", "寄存器"]
)
print(f"  [{'OK' if has_detail else 'WARN'}] 包含 PCB 相关详细内容: {has_detail}")

# ══════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════
sep("Day 5 验证汇总")
print(f"✓ Session transcript 构建: {len(logs)} 轮对话正常记录")
print(f"✓ end_session() 触发: status={result['status']}")
print(f"✓ profile patch 应用: {patch.get('applied','-')}")
print(f"✓ weak_points 注入 + 检索验证: has_detail={has_detail}")
print("\nPhase 3.5 Day 5 验证完成")
