# Phase 3.5 Lessons Learned

## 测试环境
- 课件：04-processes.pdf (44页), 05-traps.pdf (26页), 3个DOCX
- 模型：qwen-long (解析) + qwen-plus (QA/expansion/session分析)
- 日期：2026-03-08

## 1. LLM 后端迁移（Gemini → QWEN）

**问题**：Gemini free-tier key 的 `limit: 0`，无法使用。

**解决**：切换到阿里云 DashScope qwen-long（PDF理解）+ qwen-plus（对话）。
DashScope Files API 上传文件 → `fileid://xxx` 注入 system message → chat/completions。

**关键发现**：qwen-long 对中文课件的语义理解质量很好（26页→11 chunks，44页→10~24 chunks，chunk 内容准确），且无需分段批处理。

## 2. Chunk 质量（PARSE_PROMPT 调整）

**问题**：默认 keywords 只有术语，缺少"提问角度词"（区别/是什么/为什么）和对比概念（讲"进程"时不包含"程序"）。导致 Q01 "进程和程序区别" 无法检索到 "进程的定义与命名空间" chunk。

**修复**：PARSE_PROMPT 要求 keywords 至少 6 个，必须包含对比词和提问角度词。

**效果**：重新 ingest 后，"进程的定义与命名空间" chunk 的 keywords 变为：
`进程,process,程序,program,命名空间,namespace,区别,是什么,为什么,如何创建`

## 3. 检索质量（相关性阈值）

**问题**：`_merge_and_rerank` 总是返回 top_k，即使所有 chunks 的余弦距离都 > 0.5（不相关）。导致 Q01 返回的 5 个 chunks 全部 > 0.56，都是噪音。

**修复**：加入 `max_distance=0.55` 截断，过滤不相关 chunks。

**效果**：系统在课件确实没有相关内容时（如线程对比、trap table 初始化），现在正确返回少量 chunks 或空集，模型相应地如实说"课件中未找到"而非胡编。

## 4. 课件覆盖盲区（课件本身缺少的内容）

这些问题课件确实没有，属于 coverage gap，不是 retrieval bug：
- **线程** (Q08)：课件只讲单线程进程模型
- **Page fault / 非法内存响应** (Q09)：未专门讲MMU/内存保护
- **Trap table 初始化** (Q10)：课件讲了 trap 用途，但未讲 trap table 结构

模型在这些情况下的行为正确：说明找不到，并提示缺哪部分课件。

## 5. Session + Profile 机制

**验证结论**：
- `end_session()` 的 LLM 分析准确率高：4 条 patch 全部符合实际对话表现
- `+mastered` 判断保守（合理）：只有对答流畅、信息充分的问题才标记为已掌握
- `+weak_point` 判断敏感（合理）：对课件中找不到的内容、追问时概念不连贯，都会标记薄弱点
- Profile 注入效果显著：注入薄弱点后，回答从 200 字扩充到 400+ 字，自动增加 step-by-step 结构

## 6. Gotchas（坑）

| 坑 | 说明 |
|----|------|
| `GEMINIA_API_KEY` typo | 正确变量名是 `GEMINI_API_KEY`（但现已改为 QWEN） |
| DashScope json_object 模式 | 根级别必须是 `{}` 不能是 `[]`；PARSE_PROMPT 必须输出 `{"chunks":[...]}` |
| qwen-long fileid 格式 | system message 必须是 `fileid://xxx`，不是 content 里的 URL |
| ChromaDB metadata 只允许 str/int/float | keywords 列表必须转为逗号分隔字符串存储 |
| all-MiniLM-L6-v2 对中文的召回率 | 余弦距离 > 0.5 的结果基本是噪音；建议阈值 0.50-0.55 |
| SQLite WAL 必须每次连接都设置 | 不能假设上次连接设置过了 |
