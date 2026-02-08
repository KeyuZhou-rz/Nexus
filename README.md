# Nexus

MVP focus: aggregate sources, apply rules, and generate a bilingual (EN+ZH) briefing JSON via an LLM.

## Quick Start

1. Install dependencies

```bash
python -m pip install -r requirements.txt
```

2. Configure DeepSeek (OpenAI-compatible API) (optional)

DeepSeek exposes an OpenAI-compatible `/chat/completions` API. The recommended base URL is `https://api.deepseek.com`. You can also use `https://api.deepseek.com/v1` for OpenAI compatibility (the `/v1` here is not a model version). The API uses Bearer token auth and models like `deepseek-chat` or `deepseek-reasoner`.
Nexus requests strict JSON output using `response_format: {\"type\": \"json_object\"}` when `NEXUS_LLM_JSON_OUTPUT=1`.

Protocol summary:
- Base URL: `https://api.deepseek.com` (or `https://api.deepseek.com/v1`)
- Endpoint: `POST /chat/completions`
- Auth: `Authorization: Bearer <API_KEY>`
- Models: `deepseek-chat`, `deepseek-reasoner`

Set these environment variables:

```bash
export NEXUS_LLM_ENABLED="0"  # set to 1 to enable LLM summaries
export NEXUS_LLM_BASE_URL="https://api.deepseek.com"
export NEXUS_LLM_MODEL="deepseek-chat"
export NEXUS_LLM_API_KEY="<your key>"
export NEXUS_LLM_JSON_OUTPUT="1"
```

You can also copy `.env.example` to `.env` and load it:

```bash
set -a
source .env
set +a
```

3. Aggregate + generate briefing JSON

```bash
python -m nexus.briefing_cli --aggregate
```

Output:

```
/Users/rzzz/Desktop/Workspace/Projects/Nexus/data/briefing.json
```

## Briefing JSON Schema (v1.0)

```json
{
  "schema_version": "1.0",
  "generated_at": "ISO-8601",
  "window_start": "ISO-8601",
  "window_end": "ISO-8601",
  "todo": [
    {
      "text_en": "string",
      "text_zh": "string",
      "due_at": "ISO-8601 or null",
      "action_url": "url or null",
      "source_ids": ["task_id"]
    }
  ],
  "schedule": [
    {
      "text_en": "string",
      "text_zh": "string",
      "due_at": "ISO-8601 or null",
      "action_url": "url or null",
      "source_ids": ["task_id"]
    }
  ],
  "warnings": ["string"]
}
```
