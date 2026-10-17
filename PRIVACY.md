# Nexus Privacy

Nexus is local-first. Tasks, briefings, imported course files,
learning events, mastery state, OAuth tokens, and vector indexes are stored in
the configured `NEXUS_DATA_DIR` on the user's machine.

## Default behavior

- `nexus` (no command) refreshes the configured course calendars and Google
  Calendar when the last sync is more than two hours old; `nexus --offline`
  skips the refresh. It never calls an LLM.
- `nexus next`, `nexus dashboard`, `nexus context`, `nexus status`, and
  `nexus sync-status` read local snapshots and do not start synchronization or
  call an LLM.
- LLM use is disabled unless `NEXUS_LLM_ENABLED=1` is set explicitly.
- Streamlit usage statistics are disabled in `.streamlit/config.toml`.
- Nexus does not include an analytics service, advertising SDK, hosted account,
  or Nexus-operated telemetry endpoint.

## External requests

External requests happen only when the corresponding action and integration are
enabled:

| Action | Destination | Data involved |
|---|---|---|
| Brightspace iCal/RSS aggregation | Configured feed URL | Feed URL and normal HTTP metadata |
| Google authorization and sync | Google OAuth and Calendar APIs | OAuth credentials and calendar events |
| `nexus pipeline` (frozen extension) | Gmail API | Unread messages from the last 30 days, for briefings |
| Canvas sync | Configured Canvas instance | Canvas access token and course/task requests |
| Archive sync | Configured Brightspace instance | Login/session data and requested course files |
| LLM features | Configured model provider | The prompt assembled for the requested feature |
| First Chroma embedding use | Embedding model host used by Chroma | Model download request; course text remains local for local embeddings |

Feed URLs can contain access tokens. Nexus stores them locally in
`feeds.json`, treats them as sensitive input in the Dashboard, and does not
display them back after saving.

## Repository and releases

The source release excludes the entire runtime `data/` directory except for the
redacted `data/feeds.example.json` template. Automated tests reject tracked
databases, vector indexes, OAuth credentials, tokens, imported course files,
briefings, and Python bytecode.

## Removal and export

- Export assistant context with `nexus context --json`.
- Back up local learning state with `nexus db-backup`.
- Remove a runtime by deleting the specific directory configured in
  `NEXUS_DATA_DIR`. Nexus has no hosted copy to delete.

Before sharing bug reports, remove task titles, feed URLs, course documents,
OAuth files, `.env`, SQLite databases, Chroma indexes, and generated debug
screenshots.
