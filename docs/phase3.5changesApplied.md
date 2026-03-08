# Nexus Phase 3.5 — 真实数据端到端验证 变更记录

**日期**: 2026-03-08
**分支**: Nexus-Ultimate
**目标**: 用真实 OS 课件跑通完整 pipeline，发现并修复实际问题

---

## 总览：Commits

| Commit | 说明 |
|--------|------|
| `bcef53a` | 将 Gemini SDK 从 `google-generativeai` 迁移至 `google-genai` |
| `1bcde71` | 将 LLM 后端从 Gemini 切换至 QWEN（DashScope） |
| `f307f5e` | 检索质量改进：相关性阈值 + keyword 增强 |
| `c55bd35` | 添加 Day 3-4 检索测试 + Day 5 Session/Profile 测试 |
| `99fc4aa` | 添加 Lessons Learned 文档 |

---

## 变更 1：Gemini SDK 迁移（`bcef53a`）

### 背景
`google-generativeai 0.8.6` 已 deprecated，新 SDK 为 `google-genai 1.x`，API 完全不同。

### 涉及文件
- `src/nexus/knowledge/parser.py`
- `src/nexus/knowledge/query_engine.py`
- `src/nexus/knowledge/session_manager.py`

### 变更内容

**旧写法（google-generativeai）：**
```python
import google.generativeai as genai
genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-2.0-flash")
response = model.generate_content([prompt, blob], generation_config={...})
```

**新写法（google-genai）：**
```python
from google import genai
from google.genai import types
client = genai.Client(api_key=api_key)
response = client.models.generate_content(
    model="gemini-2.0-flash",
    contents=[types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt],
    config=types.GenerateContentConfig(temperature=0.1, ...),
)
```

---

## 变更 2：LLM 后端切换 Gemini → QWEN（`1bcde71`）

### 背景
Gemini free-tier key 的 `limit: 0`（`free_tier_requests` quota 耗尽），所有模型均返回 429。改用阿里云 DashScope qwen-long + qwen-plus。

### 安装
```bash
conda activate Drone
pip install google-genai litellm pypdf
```

### API 密钥
存放于 `data/QWEN_API_KEY.json`：`{"QWEN_API_KEY": "sk-..."}`

### 涉及文件及变更

**`parser.py`** — Gemini Vision → qwen-long（DashScope Files API）
- 新增 `_upload_file_to_qwen(filepath, api_key) -> file_id`：POST 到 `/v1/files`，purpose=file-extract
- 新增 `_call_qwen_with_file(file_id, api_key) -> chunks`：system message 注入 `fileid://xxx`，调用 `/v1/chat/completions`，model=qwen-long
- 删除 `_parse_with_gemini` 和 `_parse_with_gemini_batched`
- `parse_document()` 参数 `gemini_api_key` → `llm_api_key`，读 `QWEN_API_KEY` 环境变量
- `parse_method` 字段值从 `"gemini_vision"` → `"qwen_long"`

**`query_engine.py`** — Query Expansion 改用 qwen-plus（litellm）
- 删除 `_expand_query_with_gemini()`
- 新增 `_expand_query_with_qwen()`：通过 litellm + OpenAI-compatible endpoint 调用 `openai/qwen-plus`
- `QueryEngine.__init__` 参数 `gemini_api_key` → `qwen_api_key`，读 `QWEN_API_KEY`

**`session_manager.py`** — Session 分析改用 qwen-plus
- `_call_llm_litellm()` 改为调用 `openai/qwen-plus`，传入 `api_key` + `api_base`，使用 `response_format={"type":"json_object"}`
- `_call_llm_gemini()` 改为 qwen-genai client 实现
- 读 `QWEN_API_KEY` 代替 `GEMINI_API_KEY`

**`llm_client.py`** — 问答生成改用 qwen-plus
- `DEFAULT_MODEL` 从 `"gemini/gemini-2.0-flash"` → `"openai/qwen-plus"`
- `_chat_litellm()` 自动注入 QWEN `api_key` + `api_base`（当 model 以 `openai/qwen` 开头时）
- 删除 `_chat_gemini_fallback()` 方法（已无需 Gemini 直连）

**`qa_pipeline.py`** — 参数名同步
- `QAPipeline.__init__` 参数 `gemini_api_key` → `qwen_api_key`

**`ingestor.py`** — 参数名同步
- `CourseIngestor.__init__` 参数 `gemini_api_key` → `llm_api_key`
- `parse_document()` 调用改为传 `llm_api_key=`

**`parseprompt.py`** — JSON 格式调整
- 输出从 `[...]`（JSON array） → `{"chunks":[...]}`（JSON object）
- 原因：DashScope `response_format={"type":"json_object"}` 要求根级别是对象

### Ingest 验证结果
```
04-processes.pdf (44页) → 24 chunks via qwen_long
05-traps.pdf (26页)     → 11 chunks via qwen_long
os-lab-01-processes.docx → 5 chunks via qwen_long
os-pp-01-processes-solutions.docx → 16 chunks via qwen_long
os-pp-02-traps-solutions.docx → 12 chunks via qwen_long
合计: 68 chunks
```

---

## 变更 3：检索质量改进（`f307f5e`）

### 3a. 相关性阈值截断

**文件**: `src/nexus/knowledge/query_engine.py` — `_merge_and_rerank()`

**问题**: 原始 reranker 总是返回 top_k 个结果，即使所有 chunk 的余弦距离都 > 0.5（语义不相关）。导致 Q01 "进程和程序区别" 返回了 5 个距离 0.56+ 的无关 chunks。

**修复**:
```python
# 新增 max_distance 参数（默认 0.55）
ranked = [
    r for r in sorted(merged.values(), key=lambda x: x["score"])
    if r["score"] <= max_distance
][:top_k]
```

**效果**: 课件中无相关内容时（线程、page fault、trap table），返回空集或少量 chunks，模型正确说"课件中未找到"而非编造内容。

### 3b. PARSE_PROMPT keyword 增强

**文件**: `src/nexus/knowledge/parseprompt.py`

**问题**: 原始 keywords 只有术语，缺少对比概念和提问角度词。"进程的定义与命名空间" chunk 不含 "程序/program/区别"，导致 Q01 无法命中。

**修复**: 新增规则 6：
```
6. keywords 必须包含：
   - 本知识点核心术语（中英文）
   - 学生可能用来提问的词（区别/如何/为什么/比较）
   - 与本知识点对比的概念（讲"进程"时也加"程序 program"）
   - 至少 6 个关键词
```

**效果（重新 ingest 后）**:
```
"进程的定义与命名空间" keywords:
进程,process,程序,program,命名空间,namespace,区别,是什么,为什么,如何创建
```

重新 ingest 后 chunk 数量从 68 → 47（合并粒度更优）。

---

## 变更 4：测试脚本（`c55bd35`）

### `tmp/test_retrieval.py` — Day 3-4 检索质量测试
12 个真实 OS 问题（定义类/应用类/对比类/作业题类），每题检查：
- 检索到的 chunks 是否相关（topic + score）
- Query Expansion 是否有帮助
- 最终回答是否基于课件

**结果**: 12/12 有效检索，平均 4.8 chunks，平均分数 0.35-0.45（优良范围）

### `tmp/test_session_profile.py` — Day 5 Session/Profile 测试
4 轮连续对话 → end_session() → LLM 分析 → profile patch 验证：

```
applied: 4 changes
  +mastered: trap 的定义与本质（vs 普通函数调用）
  +mastered: PCB 在上下文切换中的作用与核心字段意义
  +weak_point: SYSCALL 指令硬件行为（寄存器保存细节）
  +weak_point: 僵尸进程的资源影响与回收机制
```

Profile 注入效果：注入 weak_point 后，回答长度从 200 → 400+ 字，自动加入 step-by-step 详解结构。

---

## 关键运行指令

```bash
# 环境
conda activate Drone
export QWEN_API_KEY=$(python -c "import json; print(json.load(open('data/QWEN_API_KEY.json'))['QWEN_API_KEY'])")

# Ingest 课件
PYTHONPATH=src python -m nexus.cli.ingest \
  --input data/testDataFile/ \
  --course-id CS202_OS \
  --db-dir data/chroma \
  --sqlite-path data/nexus.db

# 检索质量测试
PYTHONPATH=src python tmp/test_retrieval.py

# Session/Profile 测试
PYTHONPATH=src python tmp/test_session_profile.py
```

---

## 已知局限（Coverage Gap，非 Bug）

| 问题类型 | 说明 |
|----------|------|
| 线程 vs 进程 | 课件尚未涉及线程概念 |
| Page fault / 非法内存 | 课件未专门讲 MMU/内存保护机制 |
| Trap table 初始化 | 课件讲了 trap 用途，未讲 IDT/trap table 结构 |
| SYSCALL 寄存器细节 | 课件只给出高层语义，未列出硬件保存的具体寄存器 |

这些是 Phase 3.5 中 `+weak_point` 标记的内容来源——等后续课件补充后，ingest 新文件即可自动覆盖。
