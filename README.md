# Nexus 🧭

Nexus is a local-first learning context layer. It automatically unifies tasks, deadlines, and course updates from **Brightspace** (Learning Management System) and **Google Workspace** (Gmail, Calendar), then exposes a small, explainable learning state that can be consumed by your dashboard or another AI assistant.

It features an AI-powered daily briefing that analyzes your incoming data to highlight what's urgent, what's noise, and what requires your immediate attention. Nexus is intentionally not a chat replacement: it keeps your learning context current and portable.

## Features

- **Unified Timeline**: Merges LMS assignments and Calendar events into a single chronological view.
- **Smart Briefing**: Uses LLMs (e.g., DeepSeek, OpenAI) to generate a bilingual (EN/ZH) daily summary of tasks.
- **Google Integration**: Fetches unread course notifications from Gmail and events from Google Calendar.
- **Brightspace Integration**: Supports iCal and RSS feeds for assignments and announcements.
- **Streamlit UI**: A clean, responsive dashboard for tracking deadlines and upcoming events.
- **Canonical Context API**: Export tasks, sync health, briefing data, and learning memory as a stable JSON document.
- **Read-only Sync Control Plane**: See whether each source is configured, fresh, stale, or failed without triggering network calls.

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/nexus.git
   cd nexus
   ```

2. **Install dependencies:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   pip install -e .
   ```
   Optional feature extras (match names in `pyproject.toml`):
   ```bash
   pip install -e ".[google]"     # Gmail / Calendar integration
   pip install -e ".[archive]"    # Brightspace Playwright scraper
   pip install -e ".[pdf]"        # PDF markdown extraction (pymupdf4llm)
   pip install -e ".[llm]"        # LLM-powered features via litellm
   pip install -e ".[all]"        # Everything above
   ```

3. **Initialize the local runtime:**
   ```bash
   nexus init --data-dir data --json
   ```

   Optionally add a Brightspace calendar during initialization:
   ```bash
   nexus init --data-dir data \
     --feed-url "https://example.edu/calendar.ics" \
     --feed-name "Operating Systems" \
     --course "CS202_OS" --json
   ```

   Re-running `nexus init` is safe: existing task and feed snapshots are preserved, and a feed with the same URL is updated in place.

## Configuration

### 1. Google Workspace Setup
To enable Gmail and Calendar integration:
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **Gmail API** and **Google Calendar API**.
3. Create OAuth 2.0 Desktop credentials and download the JSON file.
4. Rename it to `google_client_secret.json` and place it in the `data/` directory.
5. Run the auth script to generate your local token:
   ```bash
   python -m nexus.google_auth
   ```
6. Keep credentials owner-readable only:
   ```bash
   chmod 600 data/google_client_secret.json data/google_token.json
   ```
   Nexus writes refreshed OAuth tokens atomically with these permissions. `nexus doctor --strict` reports unsafe permissions without printing credential contents.

### 2. Brightspace (LMS) Feeds
Create a file named `data/feeds.json` to configure your course feeds.

**Example `data/feeds.json`:**
```json
[
  {
    "kind": "brightspace_ical",
    "name": "Operating Systems",
    "url": "https://brightspace.nyu.edu/d2l/le/calendar/feed/user/...",
    "course": "CS-101",
    "enabled": true
  },
  {
    "kind": "brightspace_rss",
    "name": "Physics Announcements",
    "url": "https://brightspace.nyu.edu/d2l/le/news/rss/...",
    "course": "PHY-202",
    "enabled": true
  }
]
```

### 3. LLM Configuration (Optional)
LLM is disabled by default. To enable AI briefing explicitly, set the following environment variables (or configure them in `src/nexus/config.py` if applicable):

```bash
export NEXUS_LLM_API_KEY="your-api-key"
export NEXUS_LLM_BASE_URL="https://api.deepseek.com" # or https://api.openai.com/v1
export NEXUS_LLM_MODEL="deepseek-chat"
export NEXUS_LLM_ENABLED="1"
```

`NEXUS_LLM_ENABLED` defaults to `0`; configuring a key alone does not send LLM requests.

## Usage

### Run Integrated MVP Pipeline (Recommended)
Single command to run archive sync + post-ingest + aggregation + briefing:

```bash
nexus pipeline
```

For day-to-day use, treat `nexus pipeline` as the primary entrypoint. The
module form (`python -m nexus.cli.pipeline`) remains available for development.

Output files:
- `data/briefing.json`
- `data/pipeline_report.json`

Reference docs:
- `docs/NEXUS_REFERENCE.md`

### Running the Dashboard
Start the Streamlit interface:

```bash
streamlit run src/nexus/streamlit_app.py
```

### CLI Tools

**Generate a Briefing manually:**
```bash
nexus briefing --aggregate --window-days 7
```

**Backfill concept metadata on existing chunks:**
```bash
nexus concepts-backfill --course-id CS202_OS
```

**Generate an exam retrospective or sprint plan:**
```bash
nexus retro --course-id CS202_OS
nexus sprint --course-id CS202_OS
```

**Scan a course for knowledge gaps:**
```bash
nexus scan --course-id CS202_OS
```

**Run integrated pipeline with options:**
```bash
nexus pipeline --skip-archive-sync --window-days 7
```

Enable LLM only when needed:
```bash
nexus pipeline --llm --window-days 7
```


**Run environment doctor before aggregation (MVP):**
```bash
nexus doctor
```

**Run the offline assessment calibration gate:**
```bash
nexus calibration --fixture tests/fixtures/mastery_calibration.json --json
```

**Export the current learning context for an assistant or integration:**
```bash
nexus context --course-id CS202_OS --json
```

**Inspect source health without syncing:**
```bash
nexus sync-status --json
```

The dashboard's Home and Status views use the same read-only context snapshot. A missing Google token, LMS feed, or knowledge database is shown as a setup state; opening the UI never starts OAuth, Playwright, aggregation, or an LLM request.

The Dashboard Setup tab can initialize the same local runtime and save Brightspace iCal or RSS feeds. Tokenized feed URLs are treated as sensitive inputs and are not displayed back in the interface.

**Ingest local notes into Chroma (P2 minimal pipeline):**
```bash
nexus ingest --input ./notes --course-id EE201 --doc-type lecture_slide --db-dir data/chroma
```
Supported input suffixes: `.md`, `.txt`, `.docx`, `.pdf`.

For PDF markdown-first extraction, install optional dependency:
```bash
pip install pymupdf4llm
```


**Extract memory from conversation logs (MVP):**
```bash
nexus memory-extract --session-id demo_session --conversations-dir data/conversations --db-dir data/chroma
```

**Apply human feedback to a weak point:**
```bash
nexus memory-feedback --topic "op-amp feedback" --action accept
```

**Query memory evidence:**
```bash
nexus memory-query --query "op-amp" --db-dir data/chroma --collection nexus_memory_evidence
```

**Query local knowledge (P2):**
```bash
nexus query --query "op-amp feedback" --db-dir data/chroma --course-id EE201 --doc-type lecture_slide
```

**Read stored chunks directly (debug/review):**
```bash
nexus read --db-dir data/chroma --course-id EE201 --limit 10
```

**Refresh Google Token:**
```bash
python -m nexus.google_auth
```

## Project Structure

- `src/nexus/cli/`: CLI implementations (`pipeline`, `briefing`, `ingest`, `query`, `memory`).
- `src/nexus/aggregators/`: Modules for fetching data from Google and Brightspace.
- `src/nexus/archive_sync/`: Brightspace scraping, attachment archiving, and post-ingest trigger.
- `src/nexus/knowledge/`: Shared chunking/ingestion/query pipeline for Chroma.
- `src/nexus/intelligence/`: LLM logic for generating briefings.
- `src/nexus/streamlit_app.py`: The main dashboard UI.
- `data/`: Local storage for tasks, cache, and configuration (ignored by Git).

## License
MIT
