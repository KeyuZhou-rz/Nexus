# Dashboard V1：核心任务界面

2026-10-06 用户确认的界面契约：**有什么任务、现在做这件、自定义任务**。本文件取代旧 Home/Tasks 和 Today/State/Setup 布局。

## 页面与交互

- 左侧：未完成任务，显示标题、课程和纽约当地截止时间。按截止时间排序；已完成任务收起，来源消失的任务保留并提示核对。
- 右侧：默认显示推荐任务，可从左侧选择其他任务。只有「开始做 / 暂停」和「完成这件」两个常驻操作。长清单滚动时卡片保持可见。
- 顶部：自定义任务入口。点击后在顶部展开小窗口，只要求任务名称；课程、截止日期和预计用时可不填。
- 次要操作：当前任务内收起「修改截止与用时」，页面底部收起「课程来源」，提供同步和课程日历接入。
- 页面不显示分页、侧栏、接受/推迟按钮、知识与学习统计面板。

选择只改变当前预览，不启动计时、不改变持久化推荐。已有进行中的任务优先显示，即使重建页面也保持；暂停后可换任务。完成更新持久化状态并准备下一件。计时累计排除暂停，未开始就完成不生成虚构工时。

已有 CLI 推迟仍影响默认推荐；用户可从清单显式选择并开始。学习时间配置、知识问答、诊断、归档和其他高级能力继续通过 CLI 使用。

## 数据与服务

- `nexus.db` 的义务是页面状态源，`tasks.json` 是采集快照。
- 首次读取旧运行目录可做一次兼容迁移；普通刷新不重新覆盖手工修正。
- `build_today` 负责默认推荐和进行状态；Context 使用相同推荐模型。手工预览的任务可以与推荐不同。
- `AcademicStateService` 负责自定义任务和截止/剩余用时修正。
- `ActionService` 负责开始、暂停和完成；操作后重新计算并保存 next action。
- 仅点击更新或接入课程时调用 `run_sync`。普通渲染不发起远端同步、OAuth、归档或 LLM 调用。

同步失败保留旧记录，并在页面给出简短提示。课程日历不包含提交状态，也可能漏掉内容页任务；来源区域明确说明，可通过自定义入口补录。

## 文件

- `src/nexus/dashboard_app.py`：启动入口。
- `src/nexus/dashboard/app.py`：单页与服务调用。
- `src/nexus/dashboard/styles.py`：明暗配色、任务列表、当前卡片和窄屏样式。
- `tests/test_dashboard_app_smoke.py`：入口、自定义和来源操作。
- `tests/test_dashboard_today.py`：选择、计时、完成、重开、同名与来源消失。
- `tests/test_personal_loop.py`：完整任务闭环。

最低 Streamlit 版本为 1.41。启动和复现方式：

```bash
make dashboard
NEXUS_LLM_ENABLED=0 python -m pytest tests/test_dashboard_app_smoke.py tests/test_dashboard_today.py tests/test_personal_loop.py tests/test_release_hardening.py -q
make release-check
```

本机自动化、最低版本兼容和 Chrome 验收范围见 [TESTING.md](TESTING.md)。
