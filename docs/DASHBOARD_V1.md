# Dashboard V1

## Goal
先把 Nexus 拆出一个轻量、可调试、只读的数据看板。

这个 dashboard 只回答三个问题：

1. 我现在最该看什么？
2. 接下来几天有什么硬时间点？
3. 当前数据快照是否可信？

## Scope

Dashboard V1 只消费以下文件：

- `data/tasks.json`
- `data/briefing.json`
- `data/pipeline_report.json`

它不直接依赖：

- `memory` 状态
- `QAPipeline`
- `Chroma`
- `archive sync` 执行逻辑
- 调试面板 / ingest 面板

## Information Architecture

### `Home`
- `Focus`
  先展示 overdue / today / tomorrow 的动作项。
- `Upcoming`
  以 `Overdue / Today / Tomorrow / Next 7 Days / Later` 分组展示。
- `Briefing`
  读取现成 `briefing.json`，只展示短摘要。
- `Updates`
  展示课程公告、邮件提醒、成绩更新等“新信息”。

### `Tasks`
- 全量任务表
- 支持按 `course / source / status / tag` 过滤
- 不承担“智能解释”，只提供稳定浏览

### `Status`
- 最近一次 pipeline 是否成功
- 每个 step 的状态摘要
- 当前 dashboard 读取的文件路径

## Design Principles

### 1. Read-only first
主看板不负责复杂操作，只读现成快照，减少调试成本。

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

### 4. Small surface area
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
PYTHONPATH=src streamlit run src/nexus/dashboard_app.py
```

或：

```bash
make dashboard
```
