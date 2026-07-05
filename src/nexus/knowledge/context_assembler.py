"""
Context Assembler — Nexus Phase 3 + Phase 4A

将 retrieved chunks + user profile + graph context + user query
组装成发送给 LLM 的 messages 列表。

组装结构：
  system: 角色设定 + 教学风格（根据 user_profile.learning_style）
  user:   [课件内容块] + [知识图谱关联] + [用户画像提示] + [用户问题]

设计原则：
- 课件 context 总长度控制在 ~3000 tokens（约 6000 中文字符）以内
- User Profile 只注入与当前问题相关的字段，不全量注入
- Graph Context 只在有跨课程关联或重要前置知识时注入
- Prompt 完全可测试：输入确定 → 输出确定（无随机性）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .prompts import QA_SYSTEM_PROMPT, SOCRATIC_GUIDANCE, TEACHING_STYLE
from .query_engine import RetrievedChunk

if TYPE_CHECKING:
    from .graph_retriever import GraphContext


# 课件 context block 的近似字符上限（控制 prompt token 总量）
MAX_CONTEXT_CHARS = 6000


def _format_chunk_source(meta: dict) -> str:
    """
    格式化 chunk 来源标注，供 LLM 在回答时引用。
    示例：(来源：Lecture07_Deadlock.pdf, Lecture 7, 第 12 页)
    """
    source = meta.get("source_file", "未知文件")
    lecture = meta.get("lecture_number")
    page = meta.get("page")

    parts = [source]
    if lecture and int(lecture) > 0:
        parts.append(f"Lecture {lecture}")
    if page and int(page) > 0:
        parts.append(f"第 {page} 页")

    return "，".join(parts)


def _build_context_block(chunks: list[RetrievedChunk]) -> str:
    """
    将 retrieved chunks 组装为 context block，格式如：

    ### [来源: Lecture07.pdf, Lecture 7, 第 12 页]
    死锁的四个必要条件是：互斥、持有并等待、不可剥夺、循环等待...

    ---

    超过 MAX_CONTEXT_CHARS 时截断，避免 prompt 过长。
    """
    if not chunks:
        return "（未找到相关课件内容）"

    blocks: list[str] = []
    total_chars = 0

    for chunk in chunks:
        source_label = _format_chunk_source(chunk.metadata)
        topic = chunk.metadata.get("topic", "")
        header = f"### [{topic}]\n（来源：{source_label}）\n"
        body = chunk.content.strip()
        block = header + body

        # 超过字符上限时截断当前 chunk 并停止添加
        remaining = MAX_CONTEXT_CHARS - total_chars
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[:remaining] + "\n...（内容已截断）"
            blocks.append(block)
            break

        blocks.append(block)
        total_chars += len(block)

    return "\n\n---\n\n".join(blocks)


def _build_profile_snippet(
    profile: dict,
    user_query: str,
) -> str | None:
    """
    从 user_profile 中提取与当前问题相关的字段，注入 prompt。
    只在有相关内容时返回非空字符串（避免无用噪声）。

    注入规则（按 spec）：
    - ALWAYS: preferred_language（已在 system prompt 中处理，此处不重复）
    - 相关时注入: weak_points（与当前问题涉及的概念有交集）
    - 相关时注入: common_mistakes（与当前问题类型匹配时）
    - NEVER 全量注入：不把整个 profile 塞进 prompt
    """
    if not profile:
        return None

    snippets: list[str] = []
    query_lower = user_query.lower()

    # 检查 weak_points 是否与当前问题相关
    weak_points = profile.get("weak_points") or []
    relevant_weak = [
        wp["concept"] for wp in weak_points
        if isinstance(wp, dict) and wp.get("concept", "").lower() in query_lower
    ]
    if relevant_weak:
        snippets.append(
            f"【学生薄弱点提示】该学生在以下概念上较为薄弱，解释时请更详细：{', '.join(relevant_weak)}"
        )

    # 检查 common_mistakes 是否有参考价值
    common_mistakes = profile.get("common_mistakes") or []
    if common_mistakes:
        # 简单策略：如果 profile 中有常见错误，且问题涉及相关概念，就附加提示
        relevant_mistakes = [
            m for m in common_mistakes[:3]
            if any(word in query_lower for word in m.lower().split()[:3])
        ]
        if relevant_mistakes:
            snippets.append(
                f"【常见错误提示】注意该学生容易出现的错误：{'; '.join(relevant_mistakes)}"
            )

    return "\n".join(snippets) if snippets else None


def _build_graph_context_block(graph_context: GraphContext) -> str | None:
    """
    将图谱遍历结果渲染为 prompt 块。

    格式示例:
    [知识图谱关联]
    前置知识: 进程管理 (CS202_OS) — 理解死锁需要先掌握进程调度
    跨课程类比: 事务隔离 (CS305_DB) 类似于 互斥锁 (CS202_OS) — 都通过排他性访问防止并发冲突

    只在有实际内容时返回非 None。
    """
    if not graph_context or not graph_context.has_content:
        return None

    lines: list[str] = []

    # 前置知识
    for p in graph_context.prerequisites:
        line = f"前置知识: {p.name} ({p.course_id})"
        if p.definition:
            line += f" — {p.definition}"
        lines.append(line)

    # 跨课程关联（最高价值）
    for c in graph_context.related_cross_course:
        _type_labels = {
            "analogous_to": "类似于",
            "prerequisite": "是前置知识",
            "extends": "扩展了",
            "applied_in": "应用于",
        }
        label = _type_labels.get(c.relation_type, "关联")
        line = f"跨课程关联: {c.name} ({c.course_id}) {label} {c.relation_to}"
        if c.definition:
            line += f" — {c.definition}"
        lines.append(line)

    # 同课程相关概念（补充上下文）
    if graph_context.related_same_course:
        names = [f"{r.name} ({r.relation_type})" for r in graph_context.related_same_course[:3]]
        lines.append(f"相关概念: {', '.join(names)}")

    return "\n".join(lines) if lines else None


def _pick_socratic_level(mastery_map: dict[str, float] | None) -> str:
    """
    U1: Select Socratic guidance level based on the *average* mastery among
    concepts relevant to the current question.

    Previously used min(), so a single weak concept among several otherwise
    well-mastered ones would drag the whole turn's teaching style down to
    "critical" — average better reflects the overall relevant-concept mastery
    without letting one outlier dominate.
    If no mastery data is available, use 'unknown'.
    """
    if not mastery_map:
        return "unknown"

    avg = sum(mastery_map.values()) / len(mastery_map)
    if avg < 0.3:
        return "critical"
    if avg < 0.5:
        return "decaying"
    if avg < 0.8:
        return "stable"
    return "mastered"


def assemble_messages(
    user_query: str,
    chunks: list[RetrievedChunk],
    profile: dict | None = None,
    course_id: str | None = None,
    graph_context: GraphContext | None = None,
    mastery_map: dict[str, float] | None = None,
) -> list[dict[str, str]]:
    """
    将所有输入组装成 OpenAI 格式的 messages 列表。

    输出格式：
    [
      {"role": "system", "content": "..."},
      {"role": "user", "content": "..."},
    ]

    参数：
    - user_query: 用户原始问题
    - chunks: QueryEngine.retrieve() 返回的 RetrievalResult.chunks
    - profile: user_profile 字典（可选，不传则不注入 profile 信息）
    - course_id: 当前课程（用于 system prompt 语境说明）
    - graph_context: GraphRetriever 返回的图谱上下文（Phase 4A，可选）
    - mastery_map: {concept_name: mastery} 映射（U1，可选）
    """
    profile = profile or {}

    # ── System Prompt：角色 + 教学风格 + 苏格拉底策略 ──
    learning_style = profile.get("learning_style", "default")
    style_instruction = TEACHING_STYLE.get(learning_style, TEACHING_STYLE["default"])
    socratic_level = _pick_socratic_level(mastery_map)
    socratic_instruction = SOCRATIC_GUIDANCE.get(socratic_level, SOCRATIC_GUIDANCE["unknown"])
    system_content = QA_SYSTEM_PROMPT.format(
        teaching_style_instruction=style_instruction,
        socratic_instruction=socratic_instruction,
    )

    # ── User Message：课件内容 + 图谱关联 + Profile 提示 + 用户问题 ──
    context_block = _build_context_block(chunks)
    graph_block = _build_graph_context_block(graph_context)
    profile_snippet = _build_profile_snippet(profile, user_query) if profile else None

    # 组装 user message 各部分
    user_parts: list[str] = []

    # 1. 课件内容块（最重要）
    user_parts.append(f"[课件内容]\n{context_block}")

    # 2. 知识图谱关联（Phase 4A，可选）
    if graph_block:
        user_parts.append(f"[知识图谱关联]\n{graph_block}")

    # 3. 用户画像相关提示（可选，只有相关时才注入）
    if profile_snippet:
        user_parts.append(f"[教学提示]\n{profile_snippet}")

    # 4. 课程上下文（如果知道当前课程）
    if course_id:
        user_parts.append(f"[当前课程] {course_id}")

    # 5. 用户的实际问题
    user_parts.append(f"[学生提问]\n{user_query}")

    user_content = "\n\n".join(user_parts)

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    """
    粗略估算 prompt token 数（1 token ≈ 4 英文字符 ≈ 1.5 中文字符）。
    用于日志记录和 safety check，不需要精确。
    """
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return total_chars // 3  # 保守估算
