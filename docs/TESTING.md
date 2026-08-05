# Nexus 测试指南

所有命令在项目根目录（`Nexus/`）执行。`PYTHONPATH=src` 已内置于 Makefile，无需手动设置。

---

## 快速开始

```bash
pip install -e .       # first run: install package and CLI
make doctor          # 检查运行环境
make ui              # 启动 Streamlit 前端（http://localhost:8501）
make test            # 运行正式测试套件（默认只收集 tests/）
```

`make ui` uses `python -m streamlit`, ensuring the UI runs with the same Python
environment as the project dependencies instead of an unrelated `streamlit`
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
make ui
```

打开 `http://localhost:8501`，包含三个 Tab：

| Tab | 功能 |
|-----|------|
| 📋 Dashboard | 任务聚合、Google Calendar、每日 Briefing |
| 🎓 Study Assistant | 知识问答（基于 ChromaDB + LLM），需先 ingest 数据 |
| 📊 Learning Progress | 掌握度追踪、遗忘曲线、Gap 扫描、冲刺计划、考后复盘 |

---

## 知识模块（Study Assistant）测试流程

### Step 1：导入测试数据

```bash
# 使用 data/testDataFile/ 中的 PDF/DOCX（默认）
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

### Step 4：在前端对话测试

启动 `make ui`，进入 **Study Assistant** Tab，直接对话即可。

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

---

## 单元测试

```bash
make test              # 正式测试套件（当前 601 个测试）

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
  PYTHONPATH=src python -m nexus.cli.main doctor --no-google
NEXUS_DATA_DIR=/tmp/nexus-rc-data \
  PYTHONPATH=src python -m nexus.cli.main status --data-dir /tmp/nexus-rc-data
```

自动化覆盖位于 `tests/test_release_smoke.py`，会使用临时目录并验证
pipeline 输出、首次启动诊断以及 SQLite/Chroma 一致性审计。

---

## Makefile 变量参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `COURSE_ID` | `CS202_OS` | 课程代码（ChromaDB collection 名） |
| `INPUT` | `data/testDataFile` | ingest 输入目录或文件 |
| `DB_DIR` | `data/chroma` | ChromaDB 持久化目录 |
| `SQLITE_PATH` | `data/nexus.db` | SQLite 数据库路径 |
| `QUERY` | `What is a process...` | query 目标文字 |

覆盖方式：`make ingest COURSE_ID=EE201 INPUT=slides/`

---

## 重置数据库

```bash
make db-reset    # 删除 data/chroma/ 和 data/nexus.db（有确认提示）
```
