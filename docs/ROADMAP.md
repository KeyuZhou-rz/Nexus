# Nexus 开发路线图 (ROADMAP)

> 自动化推进积压清单。`/nexus-dev` 每次取最上方未完成项推进。
> 状态标记：`[ ]` 待办 · `[~]` 进行中 · `[x]` 完成 · `[!]` 阻塞
> 优先级：Phase T（测试地基）> Phase M（记忆模块）> Phase B（简报系统）

---

## Phase T — 测试地基（最高优先）

先把 6 个红测试修绿、补齐薄测试，为新功能开发建立安全网。

- [x] **T1 修复 chunk_text 签名断裂** [critical]
  - 现象：`chunking.py` 的 `chunk_text(text)` 仅按 `\n\n` 切分，丢失 `max_chars`/`overlap` 参数；`ingestion_service.py:67` 仍按 `chunk_text(content, max_chars=900, overlap=120)` 调用 → 6 测试全红。
  - 修复：恢复字符滑窗实现 —— 按 `max_chars` 切窗、`overlap` 重叠；`overlap >= max_chars` 抛 `ValueError`（测试 `test_knowledge_chunking.py` 已锁定该契约）。
  - 验收：`make test` 全绿；`test_knowledge_chunking.py` 两条通过。
  - 文件：`src/nexus/knowledge/chunking.py`（只读参考 `tests/test_knowledge_chunking.py`）

- [x] **T2 补强 memory_extractor 测试**
  - 现状：`tests/test_memory_extractor.py` 仅 2 条（中英模式各一）。
  - 补：空消息、无弱项信号、多条混合、证据 msg_id 透传、置信度边界。
  - 文件：`tests/test_memory_extractor.py`、`src/nexus/memory_extractor.py`

- [ ] **T3 补强 memory_update 测试**
  - 现状：`tests/test_memory_update.py` 3 条。
  - 补：合并去重、置信度下限 clamp、reject 后 review_queue 清理、decay 跨日边界。
  - 文件：`tests/test_memory_update.py`、`src/nexus/memory_update.py`

- [ ] **T4 briefing 单元覆盖**
  - 现状：仅 `test_briefing_context.py`。
  - 补：`select_exam_reminders` 窗口边界、`_is_noise` 过滤、`_rule_briefing` 降级路径、`_normalize_title`/`_contains_cjk`。
  - 文件：`tests/test_briefing_unit.py`（新建）、`src/nexus/intelligence/briefing.py`

- [ ] **T5 dashboard view_model 错误路径**
  - 现状：`tests/test_dashboard_view_model.py` 85 行，覆盖正向。
  - 补：空任务、缺失字段、时区边界。
  - 文件：`tests/test_dashboard_view_model.py`、`src/nexus/dashboard/view_model.py`

---

## Phase M — 记忆模块

- [ ] **M1 统一双记忆系统** [design]
  - 现状：根级 `memory_extractor/memory_update/state_store`（弱项 + 置信度衰减）与 `knowledge/temporal_memory/user_profile`（Ebbinghaus 掌握度 + 概念图）两套并行，未互通。
  - 决策点：对齐 schema 还是迁移合并？先由 explorer 产出依赖图与迁移方案，再定。
  - 文件：`src/nexus/memory_*.py`、`src/nexus/state_store.py`、`src/nexus/knowledge/{temporal_memory,user_profile}.py`

- [ ] **M2 temporal_memory 事件 → user_profile 自动同步**
  - `record_event` 后自动更新 `user_profile` 的 weak_points/mastered，避免双写漂移。
  - 文件：`src/nexus/knowledge/temporal_memory.py`、`user_profile.py`

- [ ] **M3 concept_extractor ↔ graph_retriever 联动测试**
  - 现各自有单测，缺端到端：抽取概念 → 构图 → 检索返回 GraphContext。
  - 文件：`tests/test_concept_graph_integration.py`（新建）

- [ ] **M4 会话结束 → 遗忘曲线 record_event 集成**
  - `session_manager` 会话分析后，按交互类型触发 `temporal_memory.record_event`（correct_answer/struggled/reviewed）。
  - 文件：`src/nexus/knowledge/session_manager.py`、`temporal_memory.py`

---

## Phase B — 简报系统

- [ ] **B1 briefing 注入 temporal 掌握度**
  - `_inject_learning_context` 已存在，当前接 `user_profile`；扩展接入 `temporal_memory.get_decaying_concepts`，把"正在遗忘"的概念塞进简报。
  - 文件：`src/nexus/intelligence/briefing.py`、`src/nexus/knowledge/temporal_memory.py`

- [ ] **B2 LLM 简报失败降级测试**
  - `_llm_briefing` 异常/超时 → `_rule_briefing` 兜底，确保不抛。
  - 文件：`tests/test_briefing_fallback.py`（新建）

- [ ] **B3 考试提醒窗口边界**
  - `select_exam_reminders(window_days=180)` 边界、跨年、无考试。
  - 文件：`tests/test_briefing_unit.py`

---

## 约定（所有 agent 必读）

- 运行：`PYTHONPATH=src` 前缀（无 pyproject.toml 安装）；`make test` / `make ui` 已就绪。
- subprocess 调用须设 `env={"PYTHONPATH": "src/", **os.environ}`。
- stdout = JSON only；调试日志 → stderr。
- `Task.from_dict` 期望 `due_at` 为 ISO 字符串；scraper 返回 datetime 须先转换。
- LLM 后端：Qwen via DashScope；key 在 `data/QWEN_API_KEY.json`，env `QWEN_API_KEY`。
- 不在未触碰代码上加多余注释/docstring；方案最小聚焦。
- 提交：每轮开 `nexus/<task-id>-<slug>` 分支，commit message 末尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
