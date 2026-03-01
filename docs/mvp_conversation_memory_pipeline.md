# MVP 对话记忆管线（已落地）

## 目标
优先打通：
- 对话日志 -> 薄弱点结构化提取 -> state.json 持久化
- 证据片段 -> Chroma `nexus_memory_evidence` 集合
- 人工 accept/reject 纠偏

## 新增组件
- `src/nexus/conversation_store.py`
  - `append_message(...)`
  - `load_messages(...)`
- `src/nexus/memory_extractor.py`
  - 规则提取 `WeakPointCandidate(topic, confidence, evidence, evidence_msg_ids)`
- `src/nexus/memory_update.py`
  - `apply_confidence_decay(...)`
  - `merge_candidates_into_state(...)`
  - `apply_feedback(...)`
- `src/nexus/memory_extract_cli.py`
- `src/nexus/memory_feedback_cli.py`
- `src/nexus/memory_query_cli.py`

## 状态结构扩展
`LearnerState` 新增：
- `weak_point_evidence`
- `weak_point_confidence`
- `weak_point_status`
- `corrections`

## Briefing 联动
- `briefing` 注入逻辑改为优先使用结构化状态：
  - 只推 active 且高置信度主题
  - fallback 到旧字段弱项列表

## 使用流程（MVP）
1. 写入或准备 `data/conversations/<session>.jsonl`
2. 执行 `memory_extract_cli`
3. 查看 `data/state.json`
4. 使用 `memory_feedback_cli` 做纠偏
5. 使用 `memory_query_cli` 查证据

## 测试
- `test_conversation_store.py`
- `test_memory_extractor.py`
- `test_memory_update.py`
- `test_state_store.py`（扩展字段覆盖）
- `test_briefing_context.py`（结构化状态联动）
