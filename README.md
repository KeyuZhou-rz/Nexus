# Nexus 🧭

Nexus is a personal academic dashboard and intelligence aggregator. It unifies tasks, deadlines, and events from **Brightspace** (Learning Management System) and **Google Workspace** (Gmail, Calendar) into a single, streamlined interface.

It features an AI-powered daily briefing that analyzes your incoming data to highlight what's urgent, what's noise, and what requires your immediate attention.

## Features

- **Unified Timeline**: Merges LMS assignments and Calendar events into a single chronological view.
- **Smart Briefing**: Uses LLMs (e.g., DeepSeek, OpenAI) to generate a bilingual (EN/ZH) daily summary of tasks.
- **Google Integration**: Fetches unread course notifications from Gmail and events from Google Calendar.
- **Brightspace Integration**: Supports iCal and RSS feeds for assignments and announcements.
- **Streamlit UI**: A clean, responsive dashboard for tracking deadlines and upcoming events.

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
   pip install -r requirements.txt
   ```

3. **Prepare Data Directory:**
   ```bash
   mkdir -p data
   touch data/.gitkeep
   ```

## Configuration

### 1. Google Workspace Setup
To enable Gmail and Calendar integration:
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **Gmail API** and **Google Calendar API**.
3. Create OAuth 2.0 Desktop credentials and download the JSON file.
4. Rename it to `google_credentials.json` and place it in the `data/` directory.
5. Run the auth script to generate your local token:
   ```bash
   python -m nexus.google_auth
   ```

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
To enable the AI briefing, set the following environment variables (or configure them in `src/nexus/config.py` if applicable):

```bash
export NEXUS_LLM_API_KEY="your-api-key"
export NEXUS_LLM_BASE_URL="https://api.deepseek.com" # or https://api.openai.com/v1
```

## Usage

### Running the Dashboard
Start the Streamlit interface:

```bash
streamlit run src/nexus/streamlit_app.py
```

### CLI Tools

**Generate a Briefing manually:**
```bash
python -m nexus.briefing_cli --aggregate --window-days 7
```


**Run environment doctor before aggregation (MVP):**
```bash
python -m nexus.doctor_cli
```

**Ingest local notes into Chroma (P2 minimal pipeline):**
```bash
python -m nexus.ingest_cli --input ./notes --course-id EE201 --doc-type lecture_slide --db-dir data/chroma
```


**Extract memory from conversation logs (MVP):**
```bash
python -m nexus.memory_extract_cli --session-id demo_session --conversations-dir data/conversations --state-path data/state.json --db-dir data/chroma
```

**Apply human feedback to a weak point:**
```bash
python -m nexus.memory_feedback_cli --topic "op-amp feedback" --action accept --state-path data/state.json
```

**Query memory evidence:**
```bash
python -m nexus.memory_query_cli --query "op-amp" --db-dir data/chroma --collection nexus_memory_evidence
```

**Query local knowledge (P2):**
```bash
python -m nexus.query_cli --query "op-amp feedback" --db-dir data/chroma --course-id EE201 --doc-type lecture_slide
```

**Refresh Google Token:**
```bash
python -m nexus.google_auth
```

## Project Structure

- `src/nexus/aggregators/`: Modules for fetching data from Google and Brightspace.
- `src/nexus/intelligence/`: LLM logic for generating briefings.
- `src/nexus/streamlit_app.py`: The main dashboard UI.
- `data/`: Local storage for tasks, cache, and configuration (ignored by Git).

## License
MIT
