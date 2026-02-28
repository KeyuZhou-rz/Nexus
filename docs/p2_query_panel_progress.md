# P2 查询与调试面板进度

## 本次新增
- 新增查询模块：`src/nexus/knowledge/query.py`
  - 支持 `course_id` / `doc_type` metadata filtering
  - 返回结构化结果（文本、metadata、distance）
- 新增 CLI：`src/nexus/query_cli.py`
  - 用于命令行快速检索 Chroma 数据
- 新增 Streamlit 侧栏调试面板（P2 Debug Panel）
  - 输入查询文本
  - 可选 course/doc_type 过滤
  - 展示 TopK 命中与 metadata

## 测试
- 新增 `tests/test_knowledge_query.py`
  - 覆盖有过滤和空结果场景

## 下一步
1. 把 query 结果接入 briefing 生成（状态+知识联合）。
2. 支持按时间范围过滤（timestamp）。
3. 增加 query 结果导出（JSON）。
