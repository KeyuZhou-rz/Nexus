---
name: nexus-test
description: Nexus 测试专项。跑全量测试、分诊失败、补薄测试、提升覆盖。优先推进 ROADMAP Phase T。
---

# /nexus-test — 测试地基维护流

跑测试、修红、补薄、提覆盖。从 ROADMAP Phase T 取任务。

## 执行步骤

### 模式 A：修红（有失败时优先）
1. `make test` 拿全量结果。
2. 对每个失败：Agent `nexus-fixer` 接管，定位根因 + 修复 + 复跑。
3. 多个失败可并行分发多个 fixer（按失败文件分组）。
4. 全绿后 Agent `nexus-reviewer` 过一遍 diff。
5. 开 `nexus/test-<slug>` 分支提交 + 标记 ROADMAP。

### 模式 B：补薄
1. 读 ROADMAP Phase T，取最上方 `[ ]`（T2–T5）。
2. 开分支。
3. Agent `nexus-explorer` 摸清被测模块契约。
4. Agent `nexus-tester` 写用例（边界、空、None、时区、中英）。
5. 跑 `make test` 确认全绿。
6. Agent `nexus-reviewer` 评审测试质量（断言是否具体、是否弱化）。
7. 提交 + 标记 ROADMAP + 停。

## 当前已知红（2026-06-30 快照）
- `chunk_text()` 签名断裂 → 6 红（T1）。优先修。

## 测试约定
- 纯单元测试不依赖网络/LLM/Qwen key，用 stub 隔离。
- `tmp_path` 处理 SQLite/文件，测试间不共享可变状态。
- 断言具体值/类型/异常，禁止 `assert result` 空泛断言。
- 不为消红删测试或硬编码期望值。

## 运行
- 全量：`make test`
- 单文件：`PYTHONPATH=src python -m pytest tests/test_xxx.py -q --tb=short`
- 覆盖率：`PYTHONPATH=src python -m pytest --cov=src/nexus tests/ -q`（若装了 pytest-cov）
