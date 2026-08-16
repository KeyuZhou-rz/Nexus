# Nexus - Stateful Study Companion

## Project Overview
RAG-based study companion for CS/EE students. Ingests course PDFs via
LLM Vision, stores as semantic chunks in ChromaDB, retrieves relevant
context for personalized Q&A.

## Commands
- `make dashboard`       # Run the Streamlit dashboard
- `make test`            # Run all tests
- `make ingest`          # Ingest PDFs/DOCX from data/testDataFile/
- `make query`           # Query the knowledge base
- `make doctor`          # Run system diagnostics
- `pytest tests/ -v`     # Run tests directly

## Architecture Rules
- ALWAYS use SQLite WAL mode for database connections
- ALWAYS use per-course ChromaDB collections, NEVER mix courses
- ALWAYS run pytest after any code change
- ALWAYS handle ChromaDB metadata as comma-separated strings (no lists)
- NEVER update user_profile per-turn; only per-session
- NEVER put entire user_profile into prompts; only relevant fields
- All prompt templates live in `src/nexus/knowledge/prompts.py`, NEVER inline

## Key Decisions
- PDF parsing: QWEN Vision first, pymupdf4llm fallback (Gemini path deleted)
- Chunking: LLM-driven semantic chunking (by knowledge point), NOT fixed-size
- Storage: ChromaDB (vectors) + SQLite (metadata + profile + logs)
- LLM gateway: litellm for model-agnostic API calls
- Profile updates: session-granularity, LLM-analyzed JSON patch

## File Structure
```
src/nexus/
  cli/            → CLI entry points (briefing, doctor, ingest, query, etc.)
  knowledge/      → PDF parse + chunk + embed + store + query + QA pipeline
  aggregators/    → Google Calendar / Brightspace data sources
  archive_sync/   → Playwright scraper for Brightspace course materials
  intelligence/   → Briefing generation + LLM utilities
  dashboard_app.py → Streamlit entry (wraps dashboard/app.py)
  pipeline.py     → Task pipeline orchestration
  config.py       → Configuration management (env vars)
```

## Gotchas
- ChromaDB metadata values must be str/int/float, NOT lists
- pymupdf4llm may return near-empty text for PPT-exported PDFs (use Vision LLM)
- SQLite requires `PRAGMA journal_mode=WAL` at every connection init
- QWEN DashScope `response_format=json_object` requires root-level JSON object,
  not array — PARSE_PROMPT output must be `{"chunks":[...]}` not `[...]`
- ChromaDB default embedding (all-MiniLM-L6-v2) is weak on Chinese;
  must use keyword enhancement to compensate
- Retrieval requires `max_distance` threshold (0.55) to avoid hallucination
  when course material doesn't cover the query
- QWEN API key stored in `data/QWEN_API_KEY.json`, injected via env var `QWEN_API_KEY`
- `parser.py` Gemini code path deleted; to restore, rewrite `_parse_with_gemini`
  using google-genai 1.x SDK (not google-generativeai)
