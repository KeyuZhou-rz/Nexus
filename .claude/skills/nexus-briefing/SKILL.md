---
name: nexus-briefing
description: Nexus 简报系统专项开发流。处理 intelligence/briefing.py 的 LLM 简报、规则降级、考试提醒、学习上下文注入。
---

# /nexus-briefing — 简报系统开发流

聚焦 `src/nexus/intelligence/briefing.py`，从 ROADMAP Phase B 取任务推进。

## 简报系统地图
`briefing.py`（636 行）核心：
- `build_briefing` / `briefing_payload` — 入口
- `_llm_briefing` — LLM 生成（DashScope qwen）
- `_rule_briefing` — 规则降级兜底
- `select_exam_reminders(window_days=180)` — 考试提醒
- `_inject_learning_context` — 注入 user_profile 学习上下文（Phase B1 扩展接 temporal_memory）
- `_is_noise` / `_has_action_keywords` / `_normalize_title` / `_contains_cjk` — 过滤与归一
- `Briefing` / `BriefingItem` — 数据结构
- `llm.py` — LLM 客户端

## 执行步骤
1. 读 ROADMAP Phase B，取最上方 `[ ]`。
2. 开 `nexus/<task-id>-<slug>` 分支。
3. Agent `nexus-explorer` 摸清 briefing 与上游（tasks 聚合）下游（CLI/UI）接口。
4. Agent `nexus-implementer` 实现。
5. Agent `nexus-tester` 补测试（B2 降级、B3 窗口边界）。
6. Agent `nexus-reviewer` 评审（重点查 LLM 失败路径与 prompt 注入安全）。
7. 提交 + 标记 ROADMAP + 停。

## 特别注意
- B1 接 `temporal_memory.get_decaying_concepts` —— 跨子系统，需确认不与记忆模块双写。
- LLM 调用必须有 `_rule_briefing` 兜底，异常不抛到调用方。
- 测试 stub 掉 DashScope，不真实调用。
- prompt 拼接用户/任务文本时检查注入风险。
