---
name: nexus-memory
description: Nexus 记忆模块专项开发流。处理 temporal_memory/user_profile/concept_extractor/graph_retriever 及根级 memory_* 的统一。调用此 skill 进入记忆子系统上下文。
---

# /nexus-memory — 记忆模块开发流

聚焦记忆子系统，从 ROADMAP Phase M 取任务推进。

## 记忆子系统地图
两套并行记忆，改动前必须确认目标侧：

**A. 根级（旧）** —— 弱项 + 置信度衰减
- `src/nexus/memory_extractor.py` — 从对话抽 weak points（中英模式）
- `src/nexus/memory_update.py` — 合并候选、apply_feedback、apply_confidence_decay
- `src/nexus/state_store.py` — LearnerState 持久化
- CLI: `memory_extract_cli / memory_query_cli / memory_feedback_cli`

**B. knowledge/（新，Phase 4）** —— Ebbinghaus 掌握度 + 概念图
- `temporal_memory.py` — 遗忘曲线 mastery(t)=init·e^(-decay·days)，事件 correct_answer/struggled/reviewed/forgot
- `user_profile.py` — weak_points/mastered/learning_style CRUD + apply_patch
- `concept_extractor.py` — 概念抽取
- `graph_retriever.py` — 概念图谱检索，返回 GraphContext
- `session_manager.py` — 会话分析 → profile patch

## 执行步骤
1. 读 ROADMAP Phase M，取最上方 `[ ]`。
2. 开 `nexus/<task-id>-<slug>` 分支。
3. Agent `nexus-explorer` 摸清 A/B 两侧影响面（重点：是否涉及双写漂移）。
4. Agent `nexus-implementer` 实现。
5. Agent `nexus-tester` 补测试（含 A↔B 联动）。
6. Agent `nexus-reviewer` 评审（重点查双写同步）。
7. 提交 + 标记 ROADMAP + 停。

## 特别注意
- M1（统一双系统）是 design 任务：先让 explorer 产出迁移方案，**停下问用户**选对齐还是合并，不擅自动 schema。
- `record_event` 改动要同步考虑 `user_profile` 更新（M2）。
- 测试用 stub 隔离 SQLite/Chroma，不调真实 LLM。
