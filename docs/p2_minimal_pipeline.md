# P2 最小链路说明（Ingest -> Chunk -> Embed -> Chroma）

## 目标
在本地实现最小可用的知识摄入链路：
- 输入 `.md/.txt`
- 文本切块（chunk）
- 本地嵌入（Hash embedding）
- 写入 Chroma 持久化库

## 模块
- `src/nexus/knowledge/chunking.py`
- `src/nexus/knowledge/embedding.py`
- `src/nexus/knowledge/store.py`
- `src/nexus/knowledge/ingest.py`
- `src/nexus/ingest_cli.py`
- `src/nexus/state_store.py`

## 使用方式
1. 安装依赖
```bash
pip install -r requirements.txt
```

2. 运行摄入
```bash
PYTHONPATH=src python3 -m nexus.ingest_cli \
  --input ./notes \
  --course-id EE201 \
  --doc-type lecture_slide \
  --db-dir data/chroma
```

3. 预期输出
```text
Ingested files: <N>, chunks: <M>
```

## 元数据字段
每个 chunk 会写入：
- `file_name`
- `doc_type`
- `timestamp`
- `course_id`
- `chunk_index`

## 状态存储
`state_store.py` 提供原子写入的学习状态存储：
- `load_state(path)`
- `save_state(path, LearnerState(...))`

## 已知限制
- 当前 embedding 为确定性 hash 向量，仅用于打通链路，不代表语义质量。
- 尚未接入 Marker/PDF 专业解析。
- 尚未在 Streamlit 中暴露检索调试面板。
