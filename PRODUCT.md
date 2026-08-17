# Nexus

> **Nexus 是学生的实时学业状态模型。**  
> 它持续整合散落在课程、邮件、Calendar、LMS、syllabus 和学生行为中的信息，维护「我现在究竟处于什么状态」，并在状态发生变化时判断什么值得注意、下一步应该做什么。

用户看见的一句话：

> **Nexus knows what you need to do, why it matters, and when you should do it.**

第一句是内部定义。第二句是用户价值。两者不要混。

Nexus 不需要比过 ChatGPT、Google Calendar、Canvas 或 Notion。它存在于这些东西**之间**。

```
Canvas ─────┐
Gmail ──────┤
Calendar ───┤
Syllabus ───┼──→ NEXUS STATE ──→ DECISION ──→ ACTION
Grades ─────┤          ↑                         │
Behaviour ──┘          └──────── OUTCOME ───────┘
```

如果用户最终能说：

> 我不需要再自己检查所有东西，再在脑子里拼凑现在该做什么——Nexus 已经知道。

产品就成立了。

---

## 真正的问题不是信息分散

表面上学生面对的是 Canvas、Gmail、Calendar、syllabus、Albert、announcement、Todo 很乱。做成「帮你集中到一个 Dashboard」只是 **aggregation**。Aggregation 已经是 commodity。

真正的问题发生在聚合之后：**所以呢？**

Deadline 是事实。Risk 是推出来的状态。Nexus 卖的是后者。

ChatGPT 的 reasoning 可以更强。它缺的是持续的学生状态：改过的截止、还剩多少工时、星期日下午有事、过去三次同类作业平均 9.2h、晚上 11 点后效率下降。Nexus 的战略不是更聪明的模型，而是给推理引擎一个更完整、会随执行结果修正的 Academic State。

LLM 是可替换的推理层。核心资产是中间几层：

```
Collectors → Normalization → Academic State Graph
           → Change Detection → Risk / Priority
           → (optional LLM) → Action Engine → Outcome → State
```

---

## 四个核心

**State.** 不是 Todo，不是 Calendar。是学生当前完整学业状态：课程、义务、考试、进度、时间、表现、习惯、外部承诺，以及它们之间的边（冲突、依赖、分配）。随使用变准。

**Change.** 大学生活一直在变：改期、新作业、取消课、出分、改要求。进入 Nexus 之后这些是 **S0 → S1**，不是「又来了一封邮件」。Nexus 关心改变了什么。

**Risk.** 不是用户自己标的 High/Medium/Low。由剩余工时、距截止、真实可用时间、课程权重、准备度、历史偏差推出来。没有可用时间就不要假装标 HIGH。

**Action.** 若永远只说「你该学 Physics」，用户还得自己打开日历。闭环是 Observe → Understand → Reason → Recommend → Apply → 看结果。Action 是抵抗通用 AI 商品化的部分。

**Feedback.** 估了 4h、用了 8h → 更新这类作业的倍率。拖了三次 → 执行风险上升，今晚先做 30 分钟，不要再整段改期。飞轮是：prediction → action → outcome，不是「我们有很多数据」。

---

## 五个界面（只这些）

1. **Today** — 不是日历。是 *What deserves my attention?* 每条带风险、原因、一个动作。
2. **Timeline** — Today / Tomorrow / This Week，按学业压力，不是纯时间序。现有「本周日程」卡片是雏形。
3. **Course State** — 这门课现在怎么样，不是资料库。
4. **Inbox / Changes** — 世界变化。默认 Nexus 已处理；用户只处理需要决策的。
5. **Plan** — 今晚时段建议，可接受/调整，最终写入日历（Calendar 回写是 Pro）。

每天成功形态可以是 30 秒：看一眼风险，点 Accept，关掉。指标不是 session duration，而是：建议是否被执行、估时/风险准不准、State 覆盖了多少真实义务、学期末是否还在。

---

## 不做什么

进不了 **State → Reason → Action → Feedback** 的，默认不做：

- AI Chat（ChatGPT 已经更好；以后可以有，不是卖点）
- 漂亮 Dashboard 当产品
- 笔记、闪卡、番茄钟、通用 Todo、AI 摘要

---

## 收费逻辑

**Free：** 聚合、日历、任务、截止、基础 Today。commodity，用来把 Academic State 养起来。

**Pro（约 $4.99/月，年付 $39–49）：** 动态风险、工时预测、个人负荷模型、自动重排、邮件/大纲变化、成绩感知规划、智能提醒、写入日历、长期行为模型。

学生价格必须低。先把一所学校打透，再 NYU → 邻近学校。不要一上来做全世界学生的 Notion。

---

## v1 砍单（已冻结）

v1 只做能转一圈飞轮的东西。Tonight 文案先不重写；先把 State 写成唯一事实。

**做**

| 步 | 内容 |
|---|---|
| S0 | Dashboard 与 CLI 默认同步对齐（不拉 Google）；`work_block` 不是义务 |
| S1a | 义务主键 = kind + 课程 + 标题，**不含** due_at；改期仍是同一条；跨源合并 |
| S1b | SQLite `obligations` 唯一写口；`tasks.json` 只是采集缓存 |
| S1c | `estimated_h / spent_h / remaining_h`；没有「今晚可学小时」就不标 HIGH |
| S2 | 最小 Change：iCal 改期 / 新作业 / 消失。发布叙事不承诺「读一封教授邮件就重排」 |
| S3 | 历史倍率可见，样本少时靠向类型先验 |
| S4 | 学生面只留 Tonight / 日程 / Setup(贴日历) / 开始-完成 |

**v1 不做**

Chat、笔记、闪卡、番茄钟、LLM 摘要当卖点、QA 主界面、自动 MFA、空位/Albert/syllabus 监控、多用户、收费代码、托管、把计划写回 Google Calendar。

**Today 交互死规定**

计时和完成必须绑在同一条义务上。标题说 CS、按钮绑 Physics 是事故，不是折中。

**工时降级**

- 有开始和结束：用实测分钟
- 只有完成：只更新提前/逾期节奏，不把 due−创建 当成工时
- 从未完成：remaining = 估计；Risk 只用截止距离，不装进度

**身份**

due_at 是属性。iCal UID 若改期后仍在，优先用来对齐；否则 `normalize(course)+normalize(title)+kind`。14 天窗内的跨源重复合成一条。

---

## 一句话战略

> Nexus maintains a continuously updated model of a student's academic state, detects changes and risks across their fragmented information sources, and converts them into timely, personalized actions.

三个词：**State → Risk → Action**。

竞争优势不是 AI、不是 Dashboard、不是 Aggregation、不是 Planning。是：

> 长期维护「你现在学业究竟处于什么状态」的结构化模型，并用你的真实执行结果不断修正它。
