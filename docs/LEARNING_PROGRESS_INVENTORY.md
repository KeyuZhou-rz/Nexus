# Learning Progress 代码清单

> 用途：在 `fix/learning-progress` 分支上慢慢修 LP 页面算法的参考地图。
> 列出所有 LP 专用文件 + 共享文件里的 LP 段落（带行号）+ 已知问题 + 修复优先级。
> 行号基于 `nexus/phase-t-finish-roadmap` (002bcea)，修复推进时请重新 grep 校准。

LP 页面 = `streamlit_app.py` 第 3 个 tab `📊 Learning Progress`（`st.tabs` 在 787 行，tab 体 1414-1778 行，到文件尾）。
核心掌握度模型 = 艾宾浩斯连续衰减（非 SM-2 间隔重复）：`mastery(t) = initial_mastery · e^(-decay_rate · days)`。

---

## A. LP 专用文件（可独立修改/回退，不影响 Dashboard / Study Assistant）

| 文件 | 作用 | 关键位置 |
|---|---|---|
| `src/nexus/knowledge/temporal_memory.py` | 艾宾浩斯衰减模型（核心） | 公式 docstring L5；事件表 L28-31；`classify_urgency` L49；`record_event` L70；`get_mastery` L146（实算 L156） |
| `src/nexus/knowledge/study_planner.py` | 学习计划（规则 + LLM） | `STUDY_PLAN_PROMPT` L32；规则优先级 `_rule_plan`；`_compute_prereq_depths`（BFS） |
| `src/nexus/knowledge/sprint_planner.py` | 考试冲刺计划（≤14 天激活） | `is_sprint_mode`；`_rule_sprint` 优先级公式；`_guess_chapter`（脆弱正则） |
| `src/nexus/knowledge/gap_scanner.py` | 知识漏洞扫描 + 弱点排名 | `scan_course`（LLM 提概念）；`compute_weakness_ranking`（`importance·(1-mastery)·hesitation·recency`） |
| `src/nexus/knowledge/hesitation_detector.py` | 犹豫检测（纯正则，22 模式） | 无 LLM |
| `src/nexus/knowledge/retrospective.py` | 考试回顾（LLM 自评命中率） | `generate_retrospective`；`hit_rate = n_hit/n_predicted`（L156 附近） |
| `src/nexus/cli/retro.py` / `scan.py` / `sprint.py` | 对应 CLI 入口 | — |
| 测试 | `tests/test_temporal_memory.py`、`test_study_planner.py`、`test_sprint_planner.py`、`test_gap_scanner.py`、`test_hesitation_detector.py`、`test_retrospective.py` | — |

`temporal_memory.py` 事件效果表（L28-31）：

| event | mastery 变化 | decay 变化 | streak |
|---|---|---|---|
| `correct_answer` | +0.15 | ×0.85 | +1 |
| `struggled` | +0.05 | ×1.10 | +0 |
| `reviewed` | +0.10 | ×0.90 | +0 |
| `forgot` | ×0.50 | ×1.20 | 重置 |

`classify_urgency` 阈值（L49）：`≥0.8 mastered / ≥0.5 stable / ≥0.3 decaying / <0.3 critical`。

---

## B. 共享文件里的 LP 段落（修复时勿伤正常代码）

### `src/nexus/streamlit_app.py`
- **LP tab 体**：L1414-1778（到文件尾）。5 块：
  - 数据加载 + 回退 L1418-1470（含 L1468/L1470 **死代码 else**，归属错块，DB 异常时页面空白无提示）
  - 4 个指标卡片（计数 + `classify_urgency`）
  - 掌握度分布条形图（硬编码阈值区间计数）
  - 概念掌握度表格（升序前 20）
  - **遗忘曲线模拟图** L1541-1553：`_decay_rate = 0.3`（L1551 硬编码），`_m0` 用当前已衰减 mastery → **双重衰减**
  - 计划按钮组：StudyPlanner / GapScanner / SprintPlanner / ExamRetrospective
- `st.tabs` 第 3 项：L787

### `src/nexus/knowledge/qa_pipeline.py`
- `_update_concept_mastery`：L581-601，调用点 L480
- L588 注释「保守策略：默认记为 reviewed」，L597-599 `record_event(event_type="reviewed")` **写死**
- `MASTERY_CLASSIFY_PROMPT` 已存在但**从未被调用** → 所有交互只发 `reviewed`，mastery 单调上升、永不衰退

### `src/nexus/knowledge/context_assembler.py`
- `_pick_socratic_level`：L181-199，`lowest = min(mastery_map.values())`（L192）
- 用法 L232 → 单个薄弱概念主导整轮苏格拉底风格

### `src/nexus/knowledge/prompts.py`
| Prompt | 行号 |
|---|---|
| `SOCRATIC_GUIDANCE` | L65 |
| `PROFILE_UPDATE_PROMPT` | L104 |
| `MASTERY_CLASSIFY_PROMPT` | L227（**未接通**） |
| `GAP_SCAN_PROMPT` | L252 |
| `SPRINT_PLAN_PROMPT` | L291 |
| `RETROSPECTIVE_PROMPT` | L339 |

（`STUDY_PLAN_PROMPT` 在 `study_planner.py:32`，不在 prompts.py）

### `src/nexus/knowledge/sqlite_store.py` + `schema.sql`
LP 相关表（schema.sql）：
- `learning_events` L140
- `concept_mastery` L154（`initial_mastery` / `decay_rate` / `last_reviewed` / `review_count` / `streak`）
- `concept_checkpoints` L213 / `checkpoint_concepts` L224
- `concept_query_log` L239（`had_hesitation` 信号）
- `exam_records` L252 / `exam_retrospectives` L264

`sqlite_store.py` 中对应 CRUD + `get_concept_query_stats`（被 gap_scanner 重复调用）。

### `src/nexus/knowledge/user_profile.py` / `session_manager.py`
- `user_profile.py`：`weak_points`/`mastered`/`learning_style`/`common_mistakes`；仅 session 粒度更新；弱项上限 30。
- `session_manager.py`：`_sessions` 是**内存 dict**（L78 附近），重启即丢 → session-end LLM 分析可能永不触发；profile 因此过时。
- profile 的 `weak_points`（自由文本概念名）与 TemporalMemory 的 `concept_mastery`（规范 concept_id）**两套系统互不同步**。

---

## C. 已知问题（按严重度）

### 🔴 严重
1. **只发 `reviewed` 事件**（qa_pipeline.py:597）——mastery 单调 +0.10 上升、decay 只 ×0.90 巩固，永不衰退/忘记。曲线模型实质失效，用得越久分类越不准。
2. **遗忘曲线图双重衰减**（streamlit_app.py:1551）——硬编码 `decay_rate=0.3` + 起点用当前已衰减 mastery。应用概念自身 `decay_rate` + `initial_mastery` 起点。
3. **两套掌握度系统不同步**——profile.weak_points ↔ TemporalMemory.concept_mastery 互不通信，Study Assistant 侧栏与 LP tab 可显示矛盾结果。
4. **session 状态内存驻留**（session_manager.py:78）——Streamlit 重启即丢，session-end profile 更新不可靠。

### 🟡 次要
5. LP tab 死代码 else（streamlit_app.py:1468/1470）——DB 异常时页面空白无提示。
6. `gap_scanner` 对每个概念调 `get_concept_query_stats` 两次（80 次冗余 SQL）。
7. `sprint_planner` 时间估算 = `daily_hours·0.75`，与概念数量/难度无关。
8. `recency_boost` 是 0/0.2 二元开关，且语义反直觉（最近查得多反而更紧急）。
9. `_guess_chapter` 正则脆弱（不匹配 Lecture/Unit/Week 命名 → 概念被冲刺计划忽略）。
10. 回顾命中率 `n_hit/n_predicted` 是 LLM 自评（predicted 和 actual 同一次 LLM 生成），非客观对照。
11. `_pick_socratic_level` 用 `min()` → 单个薄弱概念主导整轮风格。

---

## D. 修复优先级（建议顺序）

1. **接通 `MASTERY_CLASSIFY_PROMPT`**（qa_pipeline.py:581）——单点修复，最大收益。让每轮问答真正区分 correct/struggled/forgot，否则整个曲线模型失效。
2. **修遗忘曲线图**（streamlit_app.py:1551）——起点用 `initial_mastery`、decay 用概念自身 `decay_rate`，去双重衰减。
3. **统一掌握度数据源**——profile 的 weak/mastered 改为从 TemporalMemory 派生，或建立同步。
4. **session 状态持久化**到 SQLite，防重启丢失。
5. 修死代码 else、冗余 SQL、sprint 时间估算等次要问题。

---

## E. 验证方式（每步修复后）

```
PYTHONPATH=src python -m pytest tests/test_temporal_memory.py tests/test_study_planner.py tests/test_gap_scanner.py tests/test_sprint_planner.py tests/test_retrospective.py -q
PYTHONPATH=src streamlit run src/nexus/streamlit_app.py   # 确认 3 tab 正常渲染
```
