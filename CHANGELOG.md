# Changelog

## Unreleased

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
