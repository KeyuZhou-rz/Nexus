# Dashboard V1

## Goal
Nexus 的轻量、本地优先信息面板。Home 和 Status 保持只读；Tasks 可写入用户拥有的手动任务，Setup 写入来源配置。

这个 dashboard 回答四个问题：

1. 我现在最该看什么？
2. 接下来几天有什么硬时间点？
3. 当前数据快照是否可信？
4. 哪些薄弱知识与最近的课程任务直接相关？

## Scope

Dashboard V1 消费以下本地状态：

- `data/tasks.json`
- `data/briefing.json`
- `data/pipeline_report.json`
- `data/sync_receipt.json`
- `data/nexus.db` 中的学习记忆摘要

它不直接执行：

- `QAPipeline`
- Chroma 检索
- `archive sync` 执行逻辑
- 调试面板 / ingest 面板

## Information Architecture

### `Home`
- `Focus`
  先展示 overdue / today / tomorrow 的动作项。
- `Upcoming`
  以 `Overdue / Today / Tomorrow / Next 7 Days / Later` 分组展示。
- `Learning Focus`
  按掌握度和紧迫度展示弱项，并关联同课程最近的未完成任务；同时显示证据来源和可信状态。
- `Briefing`
  读取现成 `briefing.json`，只展示短摘要。
- `Updates`
  展示课程公告、邮件提醒、成绩更新等“新信息”。

### `Tasks`
- 全量任务表
- 手动添加本地任务；手动任务归用户所有，后续外部同步不会覆盖
- 支持按 `course / source / status / tag` 过滤
- 不承担“智能解释”，只提供稳定浏览

### `Status`
- 最近一次 pipeline 是否成功
- 最近一次同步的触发来源、耗时、任务数和弱项关联数
- 每个 step 的状态摘要
- 当前 dashboard 读取的文件路径

### `Setup`
- 初始化本地运行目录
- 保存 Brightspace iCal/RSS Feed
- 展示来源是否配置完成

## Design Principles

### 1. Read-only first
Home、Status 只读现成快照；Tasks 的手动录入通过独立 task service 完成；Setup 的配置写入通过 onboarding 服务完成。

### 2. Stable ranking
任务优先级固定：
- overdue
- today
- tomorrow
- next 7 days
- later

### 3. Traceable data
任何信息都需要能追溯到：
- 来源
- 时间
- 当前快照是否新鲜
- 学习判断所依据的证据类型与可信状态

### 4. Passive input is not mastery
邮件、日历和 LMS 内容只建立任务、课程与知识上下文，不会因为“看见过”某个概念就提高掌握度。只有测评或用户明确反馈形成的可信证据可以改变掌握度。

### 5. Small surface area
主界面不混入：
- session 管理
- knowledge query
- archive sync
- diagnostics / ops controls

## Code Layout

- `src/nexus/dashboard_app.py`
  Streamlit 独立入口
- `src/nexus/dashboard/app.py`
  页面渲染
- `src/nexus/dashboard/view_model.py`
  只读数据整形与分组逻辑
- `src/nexus/dashboard/styles.py`
  页面样式

## Run

```bash
nexus dashboard
```

或：

```bash
make dashboard
```
