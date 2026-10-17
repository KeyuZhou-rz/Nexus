# Nexus 路线图

> 状态标记：`[ ]` 待办 · `[~]` 进行中 · `[x]` 完成 · `[!]` 阻塞
> 已完成的 Phase T/M/B/L/U/E/X/4 记录在 git 历史里（`git log -- docs/ROADMAP.md`）。

## 定位（2026-10-17，0.4.0）

**Nexus 先给我自己用（n=1），只回答一件事：现在最值得做的那件作业是什么。** 完整定义见 [PRODUCT.md](../PRODUCT.md)。

- **产品** = `src/nexus/core/` + 终端命令：Brightspace 课程日历 / 手动任务 → `nexus.db` → 风险与可学时间 → 一件事 → `start / pause / done` 反馈。
- **形态**（P5 已决定）：终端优先，`nexus` 自动刷新并给出一件事；`nexus dashboard` 做辅助。通知和日历回写先不做。
- **扩展**（knowledge、memory、briefing、archive_sync、收件箱抽取）保留，测试保持通过，但不再继续开发。扩展可以 import core，core 不 import 扩展（`tests/test_core_boundary.py`）。
- **分发**（wheel、Windows、网络文件系统、多平台）冻结。

---

## Phase P：个人闭环（唯一主线）

- [~] **P1 自用满一周**（手动进行，从 2026-10-17 开始）
  - 先做：`nexus init --feed-url "<Brightspace 日历订阅链接>"`，然后每天至少敲一次 `nexus`，做完就 `nexus done`。
  - 记录到 `docs/DOGFOOD.md`：推荐的是不是我本来会做的那件、哪些要手动补、哪天没看以及为什么。
  - 产出：一周日志，加三条最痛的摩擦，作为后面排序的依据。

- [ ] **P2 可学时段**
  - 现状：可学时段只有演示数据会写。真实使用时风险一律是「算不出」，排序退化为过期优先、其次截止最早。
  - 做：`nexus hours`（查看、设置、清空每周可学时段），写入 `availability_rules`。
  - 验收：设好后，剩余工作超过可学时间的任务会排到前面，并显示原因。
  - 前提：P1 日志显示「按截止排」确实不够用。

- [ ] **P3 后台同步与提醒（macOS launchd）**
  - 现状：敲 `nexus` 时数据超过两小时会自动刷新；不敲就不刷新。
  - 阻碍：LaunchAgent 读不了 `~/Desktop` 下的数据（隐私限制），要先把 `NEXUS_DATA_DIR` 迁到 `~/Library/Application Support/Nexus`。
  - 做：`nexus schedule install|remove|status`，每 2 小时 `nexus sync`；可选每天早上发一条系统通知。
  - 前提：P1 日志里「忘了看」是主要原因。

- [ ] **P4 状态备份脱离 knowledge**
  - 现状：`nexus db-backup` 走 `knowledge/maintenance.py`，同时管 Chroma。
  - 做：core 自己提供只含 `nexus.db` 和任务 JSON 的备份与恢复。
  - 验收：不装 `[knowledge]` 也能备份并恢复，前后数据一致。

- [~] **P5 日历外任务与提交状态**
  - 已有：`nexus add` 补任务，`nexus done 关键词` 标记已提交。
  - 待定：要不要接 Brightspace 作业 API 拿提交状态，由 P1 日志决定。

- [x] **P6 产品形态决策**：终端优先（2026-10-17），依据见 PRODUCT.md「为什么是终端」。

---

## 冻结

- **分发**：wheel 隔离、Windows 锁、网络文件系统、Private Beta。重新启用：决定给第二个人用时。
- **知识与辅导**：ingest / query / mastery / assessment / sprint / retro。重新启用：P1 证明「不知道该复习什么」是真实缺口时。
- **简报与收件箱**：大模型简报、收件箱抽取。重新启用：同上。

---

## 约定

- 运行：`pip install -e .`（或加 `PYTHONPATH=src` 前缀）；`make test` / `make dashboard`。
- 边界：产品代码只放 `src/nexus/core/`，只 import `nexus.core`；扩展通过参数注入（`run_sync(collect=)`、`build_context(learning=)`、`GoogleAggregator(email_enricher=)`）。
- 测试由 `tests/conftest.py` 自动隔离 `NEXUS_DATA_DIR`；不要绕过它去写仓库里的 `data/`。
- stdout 只输出 JSON 或给人看的结果；调试日志写到 stderr。
- 不给没改动的代码补注释或 docstring；方案保持最小、聚焦。
- 提交：每轮开 `nexus/<task-id>-<slug>` 分支，提交信息写清改了什么、为什么。
