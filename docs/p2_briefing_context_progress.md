# P2 Briefing Context 联动进度

## 本次目标
将 P2 的状态与知识检索能力注入到 daily briefing，形成“任务 + 状态 + 知识”的复合提醒。

## 已完成
- 在 `src/nexus/intelligence/briefing.py` 新增 `_inject_learning_context(...)`：
  - 读取 `data/state.json` 的 `weak_points`
  - 自动生成弱项复习提醒（todo）
  - 使用 `knowledge.query.query_knowledge(...)` 检索 `data/chroma`
  - 将检索片段加入 briefing todo
  - 对状态加载/查询失败写入 `briefing.warnings`
- `build_briefing(...)` 已接入该注入步骤。
- 新增测试：`tests/test_briefing_context.py`

## 影响
- briefing 在原有事件提醒之外，会增加学习复习导向建议。
- 即使知识库不可用，主链路仍可继续，失败信息会以 warning 形式展示。

## 下一步
1. 将 course filter 从“课程名字符串”升级为稳定的 `course_id`。
2. 在 UI 中区分“任务提醒”和“知识复习提醒”的显示样式。
3. 将 state 的 `review_queue/mastery` 细粒度纳入注入逻辑。
