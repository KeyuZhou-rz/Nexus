---
name: nexus-review
description: 对 Nexus 当前工作区 diff 做对抗式评审 + 安全评审，提交前必跑。也可指定分支或 commit 范围。
---

# /nexus-review — 提交前评审

对当前改动做对抗式评审，提交前把关。半自动循环的默认评审环节；也可单独调用。

## 执行步骤
1. 确定评审范围：
   - 无参数 → `git diff`（工作区未提交改动）。
   - 指定分支 → `git diff main...<branch>`。
   - 指定 commit → `git show <sha>` 或 `git diff <sha>~1..<sha>`。
2. Agent `nexus-reviewer` 评审，输入 diff + 涉及文件列表。
3. 若有 blocker：
   - Agent `nexus-implementer` 或 `nexus-fixer` 修复。
   - 复跑 `make test`。
   - 再 Agent `nexus-reviewer` 复评。
   - 最多 2 轮，仍不通过则停，报告阻塞。
4. 通过 → 报告"可提交"，等用户决定是否 `/nexus-dev` 提交。

## 评审维度（reviewer 内置，此处提醒重点）
- 正确性：边界、None、空集合、off-by-one、异常路径。
- 集成断裂：签名改动是否漏改调用方（chunk_text 教训）。
- 约定：PYTHONPATH=src、stdout=JSON、subprocess env、无多余注释。
- 双写漂移：根级 memory ↔ knowledge/ temporal_memory。
- 安全：凭证、SQL 注入、路径穿越、LLM prompt 注入。

## 输出
- 必须修复（blocker）清单，每条带 file:line + 原因 + 建议。
- 建议修复（非阻塞）。
- 结论：可提交 / 需修复后复评。

只报你会行动的问题，不报风格偏好，不输出赞美。
