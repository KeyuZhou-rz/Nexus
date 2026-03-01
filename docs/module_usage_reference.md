# Nexus 模块使用文档（维护版）

## 核心模块清单

| 模块 | 作用 | 入口命令 | 输入 | 输出 |
|---|---|---|---|---|
| `nexus.pipeline` / `pipeline_cli` | 统一执行 MVP 全链路 | `python -m nexus.pipeline_cli` | 环境变量 + 本地 data | `briefing.json` + `pipeline_report.json` |
| `nexus.archive_sync` | Brightspace 抓取与附件归档 | `python -m nexus.archive_sync` | Brightspace SSO 会话 | stdout JSON（tasks/archives/failures） |
| `nexus.archive_sync.post_ingest` | 归档文档切分、入库、薄弱点提取 | 由 `archive_sync` 自动调用 | `archives` 列表 + 文件 | `chroma` + `state.json` |
| `nexus.aggregation` | Feed + Google 统一聚合 | 被 pipeline/briefing 调用 | `feeds.json` + Google token | `tasks.json` |
| `nexus.intelligence.briefing` | 生成学习简报 | `python -m nexus.briefing_cli` | tasks/state/knowledge | `briefing.json` |
| `nexus.memory_extract_cli` | 从对话提取薄弱点 | `python -m nexus.memory_extract_cli` | conversations | `state.json` + memory evidence |
| `nexus.query_cli` | 查询知识库 | `python -m nexus.query_cli` | query + chroma | top-k 检索结果 |

## 目录级规范
- `src/nexus/*_cli.py`：可直接运行的命令行入口。
- `src/nexus/archive_sync/*`：抓取、归档、后处理，仅输出结构化结果。
- `src/nexus/knowledge/*`：切分、向量化、存储与检索。
- `src/nexus/intelligence/*`：briefing 与 LLM 逻辑。
- `tests/test_*.py`：每个关键模块至少有一个行为测试。

## 新增代码块文档要求
每个新增模块必须包含：
1. 文件级 docstring（模块责任、输入输出、失败行为）
2. 对外函数 docstring（参数、返回、副作用）
3. README 或 docs 中对应使用示例
4. 至少一个自动化测试

## 常用运行命令
```bash
# 统一链路（推荐）
PYTHONPATH=src python -m nexus.pipeline_cli

# 仅 archive sync
PYTHONPATH=src python -m nexus.archive_sync

# 仅 briefing
PYTHONPATH=src python -m nexus.briefing_cli --window-days 7

# 单测
PYTHONPATH=src pytest -q
```
