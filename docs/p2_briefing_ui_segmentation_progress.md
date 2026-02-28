# P2 Briefing/UI 分区进度

## 本次完成
- 扩展 briefing context 注入策略：
  - `review_queue` 优先纳入复习提醒
  - `mastery <= 0.5` 的主题纳入弱项提醒
  - 与 `weak_points` 去重后生成 todo 建议
- Streamlit 侧栏新增 `Briefing Preview`：
  - `Task Reminders`
  - `Knowledge Reviews`
  - `Briefing Warnings`

## 关键文件
- `src/nexus/intelligence/briefing.py`
- `src/nexus/streamlit_app.py`
- `tests/test_briefing_context.py`

## 说明
当前“知识提醒”分区使用轻量规则识别（`source_ids` 为空且文本以 `Review` 开头）。后续可升级为结构化字段（如 `item_kind=knowledge`）。
