# Changelog

## Unreleased

## 0.4.0 - 2026-10-17

Terminal-first personal release: Nexus answers one question from the terminal, which coursework task to do now.

- Bare `nexus` refreshes course calendars when the last sync is over two hours old or `feeds.json` changed, then prints one task with its course, deadline and remaining hours, plus the next three. `--offline` skips the refresh.
- Added `nexus start`, `nexus pause`, `nexus done [keyword]` and `nexus add "title" --due 10-20 --hours 2`. They use the same action service and recommendation as the dashboard.
- `nexus --help` lists the product commands first and the frozen extensions separately.
- Core Google sync now reads Calendar only; inbox messages are fetched only by `nexus pipeline`.
- Fixed the inbox extraction prompt raising `KeyError` on its literal JSON braces, which failed the whole Google source whenever LLM extraction was enabled.
- Tests always run against a temporary `NEXUS_DATA_DIR`. Previously some wrote fixture tasks into the real `data/nexus.db`, where they became the top recommendation.
- Removed orphaned modules (`course_state`, `changes`, `tonight_plan`, `view_model`), phase notes, the Private Beta guide, the old reference and scratch scripts.
- Rewrote README, PRODUCT and ROADMAP around the terminal-first product.

## 0.3.0b3 - 2026-10-06

V1 engineering hardening candidate; real Brightspace/Chrome acceptance is recorded in `docs/TESTING.md`. Google/Canvas, scheduler, and first-week beta acceptance remain pending.

- Replaced the tabbed Dashboard with a task list, one current task, and optional custom entry; retained pause/resume, completion, deadline edits, and collapsed course sync.
- Added durable UI interaction tests, Streamlit 1.41 compatibility checks, and Chrome acceptance for long lists and refresh persistence.
- Unified Today and Context recommendations around canonical academic state.
- Made sync, manual edits, action feedback, and legacy completion import transactional to prevent lost progress and partially completed actions.
- Corrected pause/resume timing, total effort samples, legacy timer recovery, and defer/resume behavior.
- Preserved manual deadline corrections across stale sync, title changes, and secondary-source updates.
- Distinguished stable provider IDs for repeated assignments and retained calendar durations, cancellation, transparency, and pagination.
- Added editable deadlines and prefilled progress, including independent selection of same-title items and date-aware timezone handling.
- Extended V1, concurrency, Streamlit, and installed-package release checks; refreshed beta guidance.
- Corrected one-minute work blocks for past deadlines, removed duplicate English Today explanations, rounded displayed hours, and explained calendar coverage and submission-status limits.
- Kept dashboard text and buttons readable when the browser uses a dark theme with Nexus's light surfaces.

## 0.3.0b2 - 2026-08-08

- Added project-scoped Codex agent roles and corrected autonomous roadmap selection.
- Added a single-flight `nexus sync` service with a privacy-bounded run receipt and Dashboard `Sync now` trigger.
- Added course-aware Learning Focus cards that connect weak concepts to upcoming tasks and show their evidence provenance.
- Added Dashboard manual task entry and guaranteed that user-owned tasks survive external aggregation.
- Changed the synthetic demo to exercise the real local iCal parser before assembling learning context.
- Added isolated runtime configuration, source snapshot preservation, scheduler templates, and concurrency/release gates for headless sync.

## 0.3.0b1 - 2026-08-06

Private Beta release candidate.

- Added canonical Context API for tasks, briefing, sync health, and learning memory.
- Added normalized Brightspace, Google, and Canvas synchronization status.
- Added safe first-run initialization and Dashboard source setup.
- Added bounded, explainable mastery evidence without exposing question text.
- Added an isolated synthetic demo runtime for evaluation.
- Added offline assessment calibration and clean-room release gates.
- Added database maintenance, failure queues, and runtime diagnostics.
- Disabled default UI telemetry and excluded local runtime data from releases.
