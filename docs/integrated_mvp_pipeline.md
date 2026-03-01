# Nexus MVP 集成流水线（单入口）

## 目标
将现有能力整合为一个可维护的端到端流程：
1. Brightspace Archive Sync（含手动 SSO/Duo）
2. 归档文档解析与知识入库（Chroma）
3. 薄弱点提取与状态更新（state.json）
4. 全源任务聚合（Brightspace Feed + Google）
5. 生成 briefing.json 与 pipeline_report.json

## 统一入口
```bash
PYTHONPATH=src python -m nexus.pipeline_cli
```

输出：
- `data/briefing.json`
- `data/pipeline_report.json`

## 可选参数
```bash
PYTHONPATH=src python -m nexus.pipeline_cli \
  --skip-archive-sync \
  --archive-timeout 420 \
  --no-google \
  --no-llm \
  --window-days 7 \
  --briefing-output data/briefing.json \
  --report-output data/pipeline_report.json
```

## 关键环境变量
- `NEXUS_BRIGHTSPACE_URL`：Brightspace 基础地址
- `NEXUS_BRIGHTSPACE_LOGIN_MODE`：`manual|auto|hybrid`（默认 `manual`）
- `NEXUS_ARCHIVE_DEBUG`：是否保存调试工件（默认开启）
- `NEXUS_ARCHIVE_DEBUG_DIR`：调试工件目录（默认 `tmp/debug/archive_sync`）
- `NEXUS_ARCHIVE_POST_INGEST`：是否启用归档后处理（默认开启）
- `NEXUS_ARCHIVE_POST_INGEST_DB_DIR`：知识库目录（默认 `data/chroma`）

## 步骤契约

### Step 1: archive_sync
- 输入：Brightspace 会话 + 本地配置
- 输出：`tasks`、`archives`、`archive_failures`、`post_ingest`
- 失败策略：写入结构化错误，不污染 stdout JSON

### Step 2: aggregation
- 输入：feeds、Google 配置、已有 tasks
- 输出：去重后的 `tasks.json`
- 失败策略：当抓取失败且结果为空时，保留旧快照，防止数据被清空

### Step 3: briefing
- 输入：`tasks.json` + `state.json` + `chroma`
- 输出：`briefing.json`
- 失败策略：LLM 不可用时回退规则生成

## 运维排障
1. 先看 `data/pipeline_report.json` 每一步 `ok/message/details`
2. Archive Sync 问题看 `tmp/debug/archive_sync/*.html|*.png|*.json`
3. 数据结构问题先跑：
```bash
PYTHONPATH=src pytest -q
```

## 可维护性约定
- 所有 CLI 均返回结构化输出（JSON 或固定字段文本）
- 写盘均使用原子写接口，避免中断导致半文件
- 新增步骤必须：
  - 明确输入/输出
  - 具备失败降级策略
  - 增加至少 1 个测试
  - 更新本文件与 `README.md`
