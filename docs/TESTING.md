# Nexus 测试指南

所有命令在项目根目录（`Nexus/`）执行。`PYTHONPATH=src` 已内置于 Makefile，无需手动设置。

---

## 快速开始

```bash
python -m pip install -e ".[all]" pytest  # Python 3.10-3.13
make doctor          # 检查运行环境
make dashboard       # 启动信息面板（http://localhost:8501）
make test            # 运行正式测试套件（默认只收集 tests/）
```

`make dashboard` uses `python -m streamlit`, ensuring the UI runs with the same
Python environment as the project dependencies instead of an unrelated `streamlit`
executable found on `PATH`.

首次启用可选集成前，先检查当前环境；`--no-google` 适用于尚未配置 Google
OAuth 的本地环境。`nexus doctor` 只检查配置和本机依赖，不会连接 LLM 或同步远端数据。

```bash
nexus doctor --no-google
nexus status --json   # 只读查看最近 pipeline、失败队列和 session backlog
```

`--strict` 会将任何未完成配置或警告视为失败，适合已完成配置的部署前检查：

```bash
nexus doctor --strict
```

### Brightspace Archive 浏览器前置条件

Archive Sync 除 Python 包外还需要 Playwright 的 Chromium 浏览器。安装 optional
extra 后，在同一个虚拟环境执行一次：

```bash
pip install -e ".[archive]"
python -m playwright install chromium
```

随后运行 `nexus doctor`。若 Brightspace URL 和账号均已配置，Doctor 会将缺少浏览器
明确报告为失败，避免 `nexus pipeline` 在归档步骤才中断。

---

## 前端界面

```bash
make dashboard
```

打开 `http://localhost:8501`。V1 是单页：**有什么任务**、**现在做这件**、**＋ 自定义任务**。选择后开始/暂停或完成；截止与用时、课程来源默认收起。

```bash
make v1-acceptance   # V1 闭环测试 + 无账号 demo 证明
```

---

## V1 工程收口回归

2026-10-06 本机验证：macOS / Python 3.13，完整依赖使用独立临时虚拟环境安装。`make release-check` 全部通过，其中正式套件 **915 passed**；另外以 8 个 worker、每个写入 worker 1,000 次迭代运行并发 soak，**2 passed**。500 项不同截止时间 / 1,000 个日历区间的五次预热基准中位数为 **395.3 ms**（目标 500 ms；仅纯状态计算，不包含应用启动和数据库加载）。Brightspace 真实账号和 Chrome 验收见下文；实际调度器和首周运行尚未覆盖。

```bash
NEXUS_LLM_ENABLED=0 python -m pytest tests/test_release_hardening.py -q
make v1-acceptance
make release-check
```

新增回归全部使用临时目录和合成数据，不读取仓库运行数据、真实账号或凭证：

- 手动截止修正、重复刷新、来源只改标题/次源改动，以及稳定 UID 的同名新作业。
- 暂停后数小时刷新、恢复后完成，验证累计工时排除休息。
- SQLite 触发器故意中断完成/旧数据迁移，验证事件、状态、样本一起回滚并允许重试。
- 两个线程通过 SQLite 事务开始事件协调同步/手工编辑与完成的竞争，验证完成不会被旧状态覆盖。
- 模拟 Google Calendar 分页、改期、取消、透明事件、失败保留和成功空快照；验证真实聚合入口写入可用时间计算。
- Streamlit AppTest 操作选择、开始、暂停、恢复、完成并重建应用；通过服务设置旧推迟，验证计时与持久化闭环。
- Context 与 Today 共用风险排序；跨时区事件按真实发生时间排序。
- 过期或未知容量不把工作块压成一分钟；Today 的中文原因不重复英文说明，小时数按一位小数显示。

`wheel-smoke` 在临时虚拟环境强制安装刚构建的 wheel，从仓库外运行并确认导入路径，关闭网络和 LLM。它复用调用环境已安装的依赖，所以应先在干净、依赖完整的环境运行；`pip check` 会暴露环境里无关包的依赖冲突。

性能基准使用 500 个不同截止时间和 1,000 个日历占用区间，先预热，再运行五次并检查中位耗时低于 500 ms：

```bash
PYTHONPATH=src python benchmarks/today_capacity.py
```

共享容量区间的结果与逐个截止时间计算做精确对照，覆盖未知容量、过去/无截止时间、忙碌区间和不可用时间例外。

自动化通过不能代替真实账号、实际调度器和首周体验验收。支持的 Python 版本由 CI 矩阵覆盖；本机测试记录应注明实际版本。

### Brightspace 真实账号 / Chrome 初次验收（2026-10-06，界面改版前）

使用用户已登录的 Chrome，读取 Brightspace 的课程日历、作业栏目和课程内容页；在独立本地验收目录通过 Nexus Setup 保存现有订阅并同步。没有提交学校作业、改变学校账号设置或启用 LLM。账号、订阅地址、Cookie 和课程内容不写入版本库；原日常运行目录保持原样。

| 检查 | 方式与结果 |
|---|---|
| 四门课程来源 | 从 Brightspace 订阅窗口按课程选择，通过 Nexus Setup 保存；共收到 13 条日历记录，一门课程为空。 |
| 日期与时区 | 对照 Brightspace 日历的三个近期截止项，Nexus 显示的纽约日期、18:00 / 09:30 时间一致；截止项没有变成占用学习时间的日程。 |
| 日历覆盖缺口 | 空日历课程的 Assignments 栏目也为空，但 Content 有作业截止表，采用其他平台提交；通过 State 补录一条已发布的近期作业，验证纽约时区和日程显示。 |
| 提交状态缺口 | Brightspace 作业表明确显示一条已有提交记录；日历仍包含它。只在验收目录本地标记完成，验证重新同步后仍然完成。 |
| 重复同步 | 分别点击 UI 更新并执行一次无浏览器的 `nexus sync --data-dir <验收目录>`；成功，13 条来源记录不重复，手工条目与完成状态保留。 |
| 浏览器反馈闭环 | 在验收数据的独立副本依次推迟、接受、开始、刷新、停下、恢复、完成、刷新；按钮状态、下一条推荐、推迟状态、计时样本与持久化状态一致，休息不计入用时。副本操作不代表用户真实完成了功课。 |
| 浏览器可读性 | 截图发现深色主题与浅色页面混用，导致标签和按钮低对比度；统一文字与按钮颜色，重启应用后核验实际颜色并重新截图。 |
| Context 一致性 | 直接读取同一验收目录，在相同时间比较 Today 与 Context 的推荐义务 ID，结果一致。 |
| 数据库恢复 | 对真实验收数据库生成备份、校验校验和并恢复到新目录；14 条义务的 ID、状态和截止时间逐条一致。此项验证数据库，不声称覆盖订阅配置备份。 |

V1 日历同步只能提供日历已发布的条目，不能自动证明全部功课已经收齐或获取提交状态。当前单页的「课程来源」已说明这一限制；缺少的功课、完成状态、剩余工时和学习时间仍需要补充。真实 Google / Canvas、首次登录与 MFA、调度器长期运行和首周体验不在此次验收范围内。


### 单页界面验收（2026-10-06）

自动化使用 macOS / Python 3.13 / Streamlit 1.65 的独立完整依赖环境，关闭 LLM，测试使用临时合成数据。

- 界面与闭环定向测试：**51 passed**，覆盖空清单、自定义只填名称、同名任务独立选择、日期跨夏令时、未开始完成无虚构工时、来源消失、旧推迟和重新打开后的进行/完成状态。
- 最低支持版本 Streamlit 1.41：使用独立 SDK 依赖目录运行 dashboard smoke、dashboard today、release hardening，**43 passed**。此项仅验证 UI 兼容，不是最低版本下的真实 Google 集成测试。
- 完整 `make release-check`：正式套件 **915 passed**；另含 **85 项 V1 验收、8 项发布与隐私检查、2 项并发 soak**，隔离 wheel 安装及离线闭环检查通过。
- Chrome：加载真实 Brightspace 验收目录的 13 件未完成任务，核验文字对比度、左对齐和长清单滚动时右侧任务持续可见。
- Chrome 独立数据副本：添加无截止/用时的测试任务，选择、开始、刷新、暂停、恢复、完成、再刷新；进行中禁止冲突选择，暂停恢复选择，完成后回到下一项。
- 新界面的真实课程重复同步：四门现有订阅成功更新，13 条来源记录未重复，自定义任务及本地完成记录保留。

浏览器操作只改变独立验收数据与测试副本，不提交学校功课，也不代表用户完成了任何课程作业。原日常运行目录保持原样。窄屏样式已实现，但本次未进行独立手机尺寸浏览器验收。

---

## 知识模块（Study Assistant）测试流程

### Step 1：导入测试数据

```bash
# 使用本地 data/import/ 中的 PDF/DOCX（默认，不进入 Git）
make ingest

# 指定其他目录或课程
make ingest INPUT=slides/week3 COURSE_ID=CS301_NET

# 强制重新解析（忽略 MD5 缓存）
make ingest-force

# Legacy 模式（不走 LLM，纯文本切片）
make ingest-legacy
```

> **注意**：LLM 功能需同时设置 `NEXUS_LLM_API_KEY` 和 `NEXUS_LLM_ENABLED=1`；旧版 `data/QWEN_API_KEY.json` 仅作为兼容回退。

### Step 2：验证数据已写入

```bash
make db-stats
```

输出示例：
```
=== SQLite ===
documents|5
chunks|47
interactions|0
=== ChromaDB collections ===
  CS202_OS: 47 chunks
```

### Step 3：CLI 向量检索测试

```bash
make query QUERY="What is a process context switch?"

# 指定课程
make query QUERY="trap handling" COURSE_ID=CS202_OS
```

### Step 4：通过 CLI 问答测试

知识问答走 CLI（Dashboard 不含对话界面）：`make query QUERY="..." COURSE_ID=CS202_OS`。

---

## 任务聚合 & Briefing

```bash
# 完整流水线（含 Playwright Brightspace 爬虫 + Google）
make pipeline

# 快速模式（跳过爬虫和 Google，仅本地数据）
make pipeline-fast

# 生成每日 Briefing（规则模式）
make briefing

# 生成每日 Briefing（LLM 增强，先聚合数据）
make briefing-llm
```

### 无头采集到学习状态的闭环验收

以下流程不打开 Dashboard，也不需要真实账号。`nexus demo` 会实际解析一个本地 iCal Feed，写入统一任务快照，建立一条可信测评证据，再由 canonical context 将同课程任务与弱项关联：

```bash
nexus demo --data-dir /tmp/nexus-headless-check --json
NEXUS_DATA_DIR=/tmp/nexus-headless-check \
  nexus context --course-id CS202_OS --json
NEXUS_DATA_DIR=/tmp/nexus-headless-check \
  nexus sync-status --data-dir /tmp/nexus-headless-check --json
```

验收信号：

- task id 以 `bspace_ical:` 开头，证明经过 iCal 解析器而非预写任务；
- Brightspace 状态为 `succeeded`，并包含采集数量和最近成功时间；
- `learning.evidence` 包含 `source`、`trust_status` 和 `confidence`；
- `learning.focus[].related_task` 指向同课程的 Brightspace 任务；
- 问题正文不会出现在导出的 evidence 中。

手动任务的持久性由 `tests/test_task_service.py` 验证：成功运行外部聚合之后，`source=manual` 的项目仍然存在。

### 单实例同步验收

`nexus sync` 是定时器和 Dashboard 共用的入口：

```bash
NEXUS_DATA_DIR=/tmp/nexus-sync-check \
  PYTHONPATH=src python -m nexus.cli.main sync --no-google
cat /tmp/nexus-sync-check/sync_receipt.json
```

回执中的 `status`、`sources`、`summary.task_count`、
`summary.evidence_count` 和 `summary.linked_focus_count` 是机器可读的验收
信号。同步期间再次执行同一命令，退出码为 `2`（`busy`），不会覆盖上一次
成功回执。回执不包含 Feed URL、错误正文或测评题目正文。

---

## 单元测试

```bash
make test              # 正式测试套件

make test-knowledge    # 仅 Knowledge 模块（chunking / ingest / query）

make test-cli           # CLI dispatcher 和 --help smoke tests

make test-archive      # 仅 Archive Sync 模块

make test-fast         # 跳过有副作用的 test_archive_post_ingest
```

`tests/test_streamlit_structure.py` prevents nested Streamlit expanders, which
would otherwise fail only after a browser loads the application.
It also verifies that Study Assistant stays disabled when the default offline
mode (`NEXUS_LLM_ENABLED=0`) is active, even if an API key is present.
Idle-session finalization is deliberately scheduler-only (`nexus sessions
finalize-idle`), so loading a page never makes an LLM request for another
session.

默认 pytest 配置只收集 `tests/`。仓库中的 `tmp/` 脚本是手工实验 harness，保留用于调试但不会进入发布门禁。

直接使用 pytest 时需加 `PYTHONPATH=src`（或使用项目已安装环境）：

```bash
PYTHONPATH=src python -m pytest tests/test_knowledge_chunking.py -v
```

### Clean-room 启动验收

在全新数据目录中验证离线核心和只读运维命令：

```bash
NEXUS_DATA_DIR=/tmp/nexus-rc-data NEXUS_LLM_ENABLED=0 \
  PYTHONPATH=src python -m nexus.cli.main pipeline --skip-archive-sync --no-google
NEXUS_DATA_DIR=/tmp/nexus-rc-data NEXUS_LLM_ENABLED=0 \
  PYTHONPATH=src python -m nexus.cli.main sync --data-dir /tmp/nexus-rc-data --no-google
NEXUS_DATA_DIR=/tmp/nexus-rc-data NEXUS_LLM_ENABLED=0 \
  PYTHONPATH=src python -m nexus.cli.main doctor --no-google
NEXUS_DATA_DIR=/tmp/nexus-rc-data \
  PYTHONPATH=src python -m nexus.cli.main status --data-dir /tmp/nexus-rc-data
```

自动化覆盖位于 `tests/test_release_smoke.py`，会使用临时目录并验证
pipeline/sync 输出、同步回执、首次启动诊断以及 SQLite/Chroma 一致性审计。

完整发布门禁还会检查合成 demo 闭环、隐私文件追踪和离线测评校准：

```bash
make release-check
```

`release-check` 同时执行有界的并发 soak：多进程 ingest-like 写入、
QA-like SQLite WAL 读写、独占备份和 single-flight sync 竞争，最后验证
live/backup SQLite 完整性、外键、JSON 原子性和锁清理。发布前长时间运行：

```bash
make soak-long
```

也可显式调整 `SOAK_ITERATIONS`、`SOAK_WORKERS` 和
`SOAK_TIMEOUT_SECONDS`。soak 不访问网络、LLM、真实 connector 或仓库 `data/`。

### Assessment 离线校准

不调用 LLM，直接评估固定测评案例的分类准确率和置信度校准：

```bash
PYTHONPATH=src python -m nexus.cli.main calibration \
  --fixture tests/fixtures/mastery_calibration.json --json
```

默认门禁为 accuracy 至少 `0.8`、ECE 不超过 `0.2`；失败时命令返回非零状态。

`tests/fixtures/assessment_cases.json` 另行覆盖正确、部分正确、错误、遗忘、
提示注入和课件标准与常识冲突。该案例矩阵验证课件证据、隐藏 rubric、参考答案和
学生答案始终位于独立边界内，并且只有经过验证的评分事件才能写入 mastery；它不把
固定离线输出冒充为真实模型的语义评分质量。

---

## Makefile 变量参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `COURSE_ID` | `CS202_OS` | 课程代码（ChromaDB collection 名） |
| `INPUT` | `data/import` | ingest 输入目录或文件（本地运行数据） |
| `DB_DIR` | `data/chroma` | ChromaDB 持久化目录 |
| `SQLITE_PATH` | `data/nexus.db` | SQLite 数据库路径 |
| `QUERY` | `What is a process...` | query 目标文字 |

覆盖方式：`make ingest COURSE_ID=EE201 INPUT=slides/`

---

## 重置数据库

```bash
make db-reset    # 删除 data/chroma/ 和 data/nexus.db（有确认提示）
```
