# Nexus 测试指南

所有命令在项目根目录（`Nexus/`）执行。`PYTHONPATH=src` 已内置于 Makefile，无需手动设置。

---

## 快速开始

```bash
pip install -e .       # first run: install package and CLI
make doctor          # 检查运行环境
make ui              # 启动 Streamlit 前端（http://localhost:8501）
make test            # 运行全量单元测试
```

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
make test              # 全量（594 个测试）

make test-knowledge    # 仅 Knowledge 模块（chunking / ingest / query）

make test-cli           # CLI dispatcher 和 --help smoke tests

make test-archive      # 仅 Archive Sync 模块

make test-fast         # 跳过有副作用的 test_archive_post_ingest
```

直接使用 pytest 时需加 `PYTHONPATH=src`：

```bash
PYTHONPATH=src python -m pytest tests/test_knowledge_chunking.py -v
```

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
