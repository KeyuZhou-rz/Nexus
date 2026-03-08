PARSE_PROMPT = """你是一个课件解析器。请将课件内容转换为结构化 JSON。

规则:
1. 将内容按"知识点"切分，每个知识点是一个独立的可检索单元
2. 一个知识点 = 一个概念/定义/算法/例题/定理，大约 200-800 字
3. 如果一页包含多个知识点，输出多个对象
4. 如果一个知识点跨页，在当前页提取能看到的部分，标记 continues=true
5. 保留所有公式（LaTeX 格式）、代码块、表格

输出严格遵循以下 JSON 格式，不要输出任何其他内容:
{"chunks": [
  {
    "topic": "知识点标题 (简洁, 如 'Deadlock的四个必要条件')",
    "content": "完整的 Markdown 内容，包含公式和代码",
    "type": "definition|algorithm|example|theorem|overview|code",
    "keywords": ["关键词1", "keyword2"],
    "prerequisites": ["需要先理解的概念"],
    "continues": false
  }
]}
"""