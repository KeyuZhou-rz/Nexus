# Dashboard V1

## Goal
Nexus 的轻量、本地优先信息面板。主要视图保持只读，只有 Setup 页面写入来源配置。

这个 dashboard 只回答三个问题：

1. 我现在最该看什么？
2. 接下来几天有什么硬时间点？
3. 当前数据快照是否可信？

## Scope

Dashboard V1 消费以下本地状态：

- `data/tasks.json`
- `data/briefing.json`
- `data/pipeline_report.json`
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

### `Setup`
- 初始化本地运行目录
- 保存 Brightspace iCal/RSS Feed
- 展示来源是否配置完成

## Design Principles

### 1. Read-only first
Home、Tasks、Status 只读现成快照；Setup 的写操作通过独立 onboarding 服务完成。

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
nexus dashboard
```

或：

```bash
make dashboard
```
