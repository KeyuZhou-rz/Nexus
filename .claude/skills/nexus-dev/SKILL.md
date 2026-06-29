---
name: nexus-dev
description: Nexus 主开发编排。从 docs/ROADMAP.md 取最上方未完成任务，分发 explorer→implementer→tester→reviewer，开 feature 分支提交。半自动单步：每轮推进一个任务，提交后停下。
---

# /nexus-dev — 单步开发编排

半自动推进一个 ROADMAP 任务。用户调用后，你自动跑完整轮并在提交后停。

## 执行步骤

1. **取任务**：读 `docs/ROADMAP.md`，找最上方 `[ ]` 项（Phase T 优先于 M/B）。记下 `<task-id>` 与描述。若全 `[x]`，报告"积压已空"并停。

2. **开分支**：`git checkout -b nexus/<task-id>-<kebab-slug>`（slug 从任务标题派生，≤4 词）。

3. **探查**：用 Agent 工具调 `nexus-explorer`，传入任务 id + 描述 + 相关文件线索，拿回上下文报告。

4. **实现**：用 Agent 工具调 `nexus-implementer`，传入任务 + explorer 报告。等其完成并自测。

5. **测试**：用 Agent 工具调 `nexus-tester`，要求为新改动补测试并跑 `make test`。若有红：
   - 代码 bug → 回 `nexus-implementer` 或 `nexus-fixer`。
   - 测试错 → tester 自修。
   - 最多重试 2 轮，仍红则停，报告阻塞。

6. **评审**：用 Agent 工具调 `nexus-reviewer`，传 `git diff`。若有 blocker → 回 implementer 修，复评；最多 2 轮。

7. **提交**：
   - `git add -A`
   - `git commit -m "<type>(<scope>): <task-id> <摘要>\n\n<Body 列改动>\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"`
   - type 用 feat/fix/test/docs/refactor。

8. **更新 ROADMAP**：把该任务 `[ ]` 改为 `[x]`，commit 该改动（amend 或单独 docs commit）。

9. **停**：报告本轮完成情况 + 下一任务是哪个，等用户再次 `/nexus-dev`。

## 红线
- 一轮只推一个任务，不串跑。
- 提交前必须有 reviewer 通过 + `make test` 全绿。
- 遇 design 决策点（如 M1）停下问用户，不擅自定架构。
- 不推到 main，只在 `nexus/*` 分支提交。

## 失败处理
- 探查发现任务描述与代码现状矛盾 → 停，报告差异。
- 实现超范围 → 停，请用户确认范围。
