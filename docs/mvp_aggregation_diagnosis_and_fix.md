# MVP 聚合故障诊断与修复

## 诊断结论
- 聚合链路中 Google 依赖缺失会触发 `Google error`。
- `data/feeds.json` 缺失/未配置时 Brightspace 无数据源。
- 在上述场景下，聚合可能写回空任务列表，导致 `tasks.json` 被清空。

## 本次修复
- `src/nexus/aggregation.py`
  - 增加 Brightspace 源未配置提示：`Brightspace feeds not configured or disabled.`
  - 增加失败回退保护：
    - 当抓取结果为空且来源报错时，保留并写回已有 `tasks.json` 快照
    - 附加错误信息：`preserving existing tasks.json`

## 测试覆盖
- `tests/test_aggregation_resilience.py`
  - 源失败时保留旧任务
  - 无 feed 配置时返回明确错误

## MVP意义
这次修复优先保证“数据不丢失”，避免操作失败导致任务数据被覆盖为空，满足 MVP 对稳定性的基本要求。
