# Nexus 0.3 Private Beta

## Product boundary

Nexus maintains local academic state through a simple task list and current-task interface. The Private Beta validates one loop:

1. collect or enter courses, obligations, due dates, and study availability;
2. persist a single canonical state and its deltas;
3. rank one next action from availability-aware risk;
4. record start / pause / complete against the same obligation so the
   next read changes.

Ask testers whether the current state is credible, whether the default current-task
recommendation is the thing most worth doing now, and whether completing work
changed the next recommendation. Do not score the beta on visual polish.

The release intentionally excludes multi-user hosting, mobile apps, browser DOM
injection, billing, real-time webhooks, and automated MFA handling.

## Supported environment

- Python 3.10-3.13
- macOS and Linux on a local filesystem are the supported runtime platforms
- One user-owned `data_dir` per machine, used by the Dashboard and every scheduler
- Windows UI/import compatibility is best-effort; lock-dependent sync, ingest,
  and backup commands are unavailable in this beta
- Local Streamlit Dashboard

Do not place `data_dir` on NFS, SMB, iCloud Drive, Dropbox, or another
networked/synchronized filesystem. Do not share it across hosts or OS users.
The release uses POSIX file locks and owner-only sidecars to protect local
writes. When changing the service account, stop every Nexus process first and
transfer ownership of the complete data directory.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install nexus_dashboard-0.3.0b3-py3-none-any.whl
# Keep every Nexus command on one explicit, absolute local runtime.
export NEXUS_DATA_DIR="$(pwd -P)/nexus-data"
nexus init --data-dir "$NEXUS_DATA_DIR" --json
nexus dashboard
# After Nexus has created nexus.db, this inherits the same runtime directory.
nexus db-backup
```

Keep `NEXUS_DATA_DIR` exported in every terminal or scheduler that runs Nexus.
Both `nexus dashboard` and the default `nexus db-backup` paths inherit it; do
not start either command with an unrelated working-directory-local `data/`.

Install optional integrations only when needed:

```bash
python -m pip install "nexus-dashboard[google]"
python -m pip install "nexus-dashboard[pdf]"
```

The Playwright archive extra is advanced mode and is not required for the
first-run experience.

## Evaluate without accounts

```bash
nexus demo --data-dir /tmp/nexus-demo --json
nexus demo --scenario v1-loop --data-dir /tmp/nexus-v1 --json
NEXUS_DATA_DIR=/tmp/nexus-demo nexus dashboard
NEXUS_DATA_DIR=/tmp/nexus-demo nexus context --course-id CS202_OS --json
```

The `v1-loop` scenario needs no Google/Brightspace account and no network. It
proves deadline change, calendar conflict, high-risk rerank, one action, and
completion feedback. Nexus cannot yet infer remaining effort or availability
from a calendar that only has deadlines; those still need a manual estimate and
study windows.

The demo command refuses non-empty destinations and generates synthetic data
only. Its Brightspace sample is still synthetic, but it is processed through
the same local iCal parser used by a configured feed.

## Beta acceptance checklist

The b3 candidate closes automated engineering acceptance. On 2026-10-06, a logged-in Chrome session and an isolated local runtime verified four real Brightspace feeds, repeated sync, manual entry, completion persistence, browser timing feedback, and database backup/restore. Calendar feeds can omit obligations and do not include submission status; manual input remains necessary. Real Google/Canvas, the local scheduler, and a week of student use remain release acceptance work. See `docs/TESTING.md` for scope and evidence.

- `nexus init` succeeds in a new directory; Dashboard opens without sync or an LLM call.
- With networking disabled, use **＋ 自定义任务** to add two tasks. Only a title is required; the page offers one current task without asking for priority. Availability rules are configured through the CLI.
- Correct a deadline or remaining time under **修改截止与用时**. After reopening, the default current task and `nexus context --json` agree. Selecting a task only previews it until Start.
- Select, start, pause, resume, and complete work. Breaks do not become measured effort; completing an item selects another.
- Existing CLI deferrals are respected by the default recommendation; explicitly selecting a deferred task permits starting it.
- Two same-title obligations can be selected independently. A new provider UID does not inherit an earlier item's completion or time.
- Configure one real Brightspace feed or Google integration. Use `nexus sync --google` when Google is enabled; otherwise use `--no-google`.
- Verify a busy calendar event reduces the overlap with study time. Moving, cancelling, or marking it free updates capacity.
- A failed source retains its last successful data and reports failure. A successful empty source marks disappeared obligations as missing; the task list keeps them available for explicit review.
- Close and reopen the Dashboard. Progress, completion, and measured effort persist.
- `nexus doctor` has no failures for the integrations actually enabled.
- `make release-check` passes; exercise the configured scheduler and backup/restore on a disposable runtime.

For the first week, record installation problems, incorrect source observations, recommendations you disagree with, and whether completing work improves the next recommendation. Remaining effort and study windows still require user input; a deadline calendar alone cannot supply them.

## Release channels

The initial channel is a GitHub pre-release containing the wheel, source archive,
changelog, privacy policy, and a short demo video. Invite a small cohort before
broader launch. Product Hunt and public marketplace distribution should wait
until at least five beta users complete the collection-to-context loop.

Collect only operational feedback needed for the beta: installation success,
time to first source, first useful dashboard view, sync failures, and whether the
exported context improved an external assistant session. Do not collect course
content or credentials.
