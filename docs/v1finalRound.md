# Nexus V1 Final Round — Implementation Plan

> **Status:** implementation contract for the pre-semester V1 demo  
> **Scope:** turn the current, working local-first prototype into one reliable
> **State → Delta → Risk → Recommendation → Action → Feedback** loop.  
> **Last repo audit:** 2026-09-01, commit `c4f050f`; `python -m pytest -q`:
> **816 passed**.

---

## 1. Product contract

### 1.1 The V1 promise

After using Nexus for one real week, a student should be able to say:

> “I do not have to continually check and mentally combine my academic
> information. Nexus knows the one thing most worth handling now.”

Nexus is **not** a dashboard, a generic to-do list, an AI chat surface, or a
calendar replacement. It maintains a longitudinal, explainable academic state
and converts changes in that state into an actionable next step.

The V1 demo succeeds only when all four statements below are true:

1. **State is credible.** Courses, obligations, due dates, progress, calendar
   commitments, and available study time form one current state.
2. **Change propagates.** A deadline edit, completion, progress update, or
   calendar conflict changes persisted state and the next recommendation.
3. **Recommendation is useful.** Today ranks the current state, explains why,
   and offers one concrete action—not a fixed template or a hand-entered
   priority label.
4. **Feedback changes future decisions.** Accepting, starting, completing, or
   deferring an action is recorded against the same obligation and affects later
   effort/risk prediction.

### 1.2 Product loop

```text
Courses / LMS / manual obligations / calendar / availability
                            │
                            ▼
                   Canonical academic state
                            │
                   state version / delta
                            ▼
            deterministic risk + recommendation engine
                            │
                            ▼
            Today: one next action and its explanation
                            │
                    accept / start / complete / defer
                            ▼
              feedback event + updated state + re-ranking
```

The rule-based engine is the V1 decision maker. LLM output may enrich copy in a
future release, but it must never be required to calculate risk, rank Today, or
persist a student action.

### 1.3 Decisions already made

These are implementation constraints, not open product questions.

| Decision | V1 rule |
|---|---|
| Main surface | `Today` is the default and primary screen. |
| Core object | An academic obligation, not a collector `Task`, is the unit of state, recommendation, timer, completion, and feedback. |
| Priority | The user-entered `Task.priority` field is not an input to V1 ranking. It may remain only for backward-compatible import/display until removed. |
| Risk | Derived from due time, remaining effort, and available study time; no available-time evidence means no `high` risk label. |
| Manual entry | Allowed and first-class for obligations, progress, and availability. Completeness of the loop is more important than connector coverage. |
| Calendar | Used to establish occupied time and available study capacity, not merely shown as an events list. |
| Action | Must name a specific obligation and a concrete first step/block length. A generic “study course X” message is invalid. |
| Feedback | Accept, start, complete, and defer events are durable, attributable to an obligation, and visible to the decision engine. |
| UI | Keep only Today and a thin Setup/State management surface in the V1 flow. Existing knowledge, chat/query, notes, flashcards, Pomodoro, and dashboard-style browsing are not V1 work. |
| Privacy | Keep the existing local-first, single-user runtime contract. No hosted service, telemetry of course content, or new external account is needed. |

---

## 2. Existing system: audited starting point

The repository is close enough that V1 is a convergence project, not a rewrite.
The table distinguishes assets to preserve from gaps that must be closed.

| Area | Current implementation | V1 implication |
|---|---|---|
| Collector cache | `Task` records are aggregated into `tasks.json`; Brightspace, Canvas, Gmail, Google Calendar, and manual tasks are supported. | Keep collectors and `tasks.json` as ingestion cache only; do not make it the source of truth. |
| Canonical obligations | `academic_state.py` persists `obligations` in `nexus.db`; identity is `kind + normalized course + normalized title`, deliberately excluding `due_at`; cross-source grouping and disappeared-change reconciliation exist. | Preserve this foundation, but make the obligation ID—not a source task ID—the action/feedback key. |
| Change detection | `obligation_changes` records `created`, `due_changed`, and `disappeared`; `changes.py` supports acknowledgement. | Convert each meaningful state change into a recomputation trigger and expose it on Today only when it changes a decision. |
| Effort history | `workload.py` has type priors and a shrinkage/median estimator; `task_service.py` records started/completed time in `completions.json`. | Move durable feedback to SQLite and calculate remaining effort per obligation. Retain/migrate existing completion samples. |
| Current recommendation | `build_judgment()` ranks mostly by nearest actionable deadline; `tonight_plan.py` picks a local evening window. | Replace the deadline-first main ranking with availability-aware risk. The existing judgment may remain as a compatibility adapter during migration only. |
| Risk | `risk_band` is stored, but current code intentionally emits only `overdue` or `unscored` because available time is absent. | Add real availability input and a pure, tested risk engine. |
| Current UI | Streamlit tabs are `Tonight`, `Tasks`, `Setup`; Tonight has plan acceptance, change acknowledgement, and start/complete buttons. | Recompose existing interaction pieces around `Today`; do not build a new visual product before the state contract exists. |
| Course state | `course_state.py` aggregates open obligations and remaining hours by course. | Keep as a supporting read model, not a separate source of truth or first-screen dashboard. |
| Demo and tests | `nexus demo` creates synthetic course data; tests cover identity, due changes, completion timing, effort history, plan acceptance, and dashboard smoke. Full suite passes 816 tests. | Extend the demo into a deterministic V1 acceptance fixture and add state/risk/action/feedback tests. |

### 2.1 Important current limitations

These limitations are intentional targets of this plan and must not be hidden by
UI copy:

- `Task.to_dict()` does not persist its effort/risk fields, so collector records
  cannot be trusted as the full state.
- `completions.json` is keyed by source task ID. An external source ID can
  change, while an obligation is deliberately stable across due-date changes and
  cross-source merging.
- `build_judgment()` ranks deadline proximity and coarse effort but receives no
  available-time capacity.
- `Tonight` acceptance is stored separately in `tonight_plan.json`; it is not a
  general action-event history and acceptance does not yet change risk.
- The manual add form still asks for a user priority. That conflicts with the
  V1 risk contract.
- Google Calendar events are currently useful as collected records, but the
  `Task` model has no start/end-duration contract from which capacity can be
  calculated.
- The current UI shows several useful modules at once (course state, schedule,
  task list, changes), so the user still has to decide what matters.

### 2.2 Preserve these compatibility boundaries

- Existing connector tests, privacy controls, locks, migrations, CLI commands,
  and knowledge subsystem behavior must remain green.
- `tasks.json` stays supported as a collector snapshot and import bridge.
- Existing task completions must not disappear during migration. They are
  imported once into canonical action/feedback records idempotently.
- The current stable obligation identity rule is retained for V1. If identity is
  ambiguous, create a reviewable separate obligation rather than silently
  merging unrelated work.

---

## 3. Target architecture

### 3.1 Ownership and write paths

```text
Collectors + manual forms
  └─ Task snapshots / calendar observations (replaceable inputs)
       └─ AcademicStateService.ingest_snapshot()        [only state write path]
            ├─ obligations, source_observations
            ├─ calendar_blocks, availability_rules
            ├─ state_changes
            └─ state revision
                    │
                    ▼
             RiskEngine.evaluate(state, now)            [pure read]
                    │
                    ▼
          RecommendationService.recommend(state, risk)  [pure read]
                    │
                    ▼
            Today view model / CLI demo                  [read-only]
                    │
                    ▼
             ActionService.record_event()               [only feedback write path]
                    ├─ action_events
                    ├─ obligation progress/status
                    └─ effort samples / execution signals
```

`AcademicStateService` and `ActionService` are service boundaries, not a demand
for a framework rewrite. They may be implemented as modules in `src/nexus/` and
use the existing `SQLiteStore` migration/locking conventions.

### 3.2 Read/write rules

1. **Collectors never directly decide risk or Today order.** They only provide
   observations.
2. **Only canonical state writes create deltas.** A manual edit goes through the
   same write path as a sync-derived change.
3. **Risk/recommendation are pure calculations from an explicit state snapshot
   and `now`.** No hidden clock, file write, HTTP call, or LLM call is allowed.
4. **User interaction is event-sourced at the boundary.** A UI button records
   an action against an obligation ID, updates the state transactionally, and
   re-reads Today. It must not mutate a view-model list in memory.
5. **A recommendation is disposable.** Persisting a rendered recommendation is
   optional audit data; its authoritative inputs are state and action events.
6. **The same stable obligation ID is used in recommendation, start timer,
   completion, remaining-effort update, and feedback history.** No button may
   operate on another task merely because it has a similar title.

### 3.3 Incremental implementation order

Do not start with a visual redesign. Implement in this order:

1. Canonical schema and state migration.
2. Availability/capacity and deterministic risk calculation.
3. Recommendation and action-event feedback loop.
4. Today view model and minimal Streamlit surface.
5. Deterministic demo, acceptance tests, and beta rehearsal.

Every phase must leave the suite passing and be releasable on its own. Avoid a
long-lived branch that changes schema, UI, and collectors without intermediate
tests.

---

## 4. Target data model

All timestamps are timezone-aware ISO-8601 strings. SQLite remains the durable
state store; JSON files remain compatibility/import artifacts only.

### 4.1 Canonical entities

| Entity | Required fields | Purpose |
|---|---|---|
| `courses` | `id`, `name`, `active`, `created_at`, `updated_at` | Normalized course identity and display name. V1 may derive rows lazily from existing course strings. |
| `obligations` | existing stable `id`, `title`, `course_id/course`, `kind`, `due_at`, `status`, `estimated_hours`, `spent_hours`, `remaining_hours`, `last_seen_at`, `updated_at` | Current academic work that can be recommended. `due_at` is an attribute, never identity. |
| `obligation_sources` | `obligation_id`, `source`, `external_id`, `observed_at`, `payload_hash`, `is_primary` | Normalized source-to-obligation mapping. Replaces a JSON-only `source_ids` list as the queryable provenance record; retain the existing column during migration. |
| `calendar_blocks` | `id`, `source`, `external_id`, `title`, `starts_at`, `ends_at`, `course_id?`, `kind`, `status`, `observed_at` | Occupied events and explicit study blocks. This has real duration, unlike the legacy deadline-shaped `Task`. |
| `availability_rules` | `id`, `weekday`, `starts_local`, `ends_local`, `timezone`, `enabled` | Manual recurring study availability. This is the V1 path to a trustworthy capacity model before every calendar connector is complete. |
| `availability_overrides` | `id`, `starts_at`, `ends_at`, `kind` (`available`/`unavailable`), `note?` | One-off student input; allows “I am free Saturday 14:00–18:00” and exceptions. |
| `state_changes` | `id`, `entity_type`, `entity_id`, `kind`, `before_json`, `after_json`, `created_at`, `source` | Auditable delta stream. V1 kinds include `created`, `due_changed`, `progress_changed`, `calendar_changed`, `completed`, and `disappeared`. |
| `action_events` | `id`, `obligation_id`, `recommendation_key?`, `event_type`, `occurred_at`, `payload_json` | Student feedback. Event types: `recommended`, `accepted`, `started`, `stopped`, `completed`, `deferred`, `dismissed`. |
| `effort_samples` | `id`, `obligation_id`, `course_id?`, `kind`, `minutes`, `completed_at`, `source` | Clean historical observations used by effort estimation. Only actual elapsed/manual time creates a sample. |

### 4.2 Obligation status and progress

Allowed V1 values:

| Field | Values | Rules |
|---|---|---|
| `status` | `open`, `in_progress`, `completed`, `cancelled`, `missing` | `completed` and `cancelled` never appear in active Today ranking. `missing` requires acknowledgement, never silent deletion. |
| `progress_percent` | integer 0–100, nullable | Manual V1 input. `100` records completion only after the user confirms completion; it does not silently create an effort sample. |
| `estimated_hours` | positive decimal, nullable | Manual estimate wins; otherwise the effort estimator produces an effective estimate with a basis. |
| `spent_hours` | non-negative decimal | Sum of measured/manual effort events. |
| `remaining_hours` | non-negative decimal, nullable | `max(effective_estimate - spent, 0)` unless the student explicitly sets remaining effort. |

Add `progress_percent` and `remaining_override_hours` through a migration rather
than encoding them in `notes` or JSON sidecars. Existing `risk_band` may remain
as an indexed/display cache, but the engine calculation is authoritative.

### 4.3 Availability semantics

For an obligation due at `D`, **available time** is the union of:

1. enabled recurring availability rules between `now` and `D`;
2. explicit available overrides;
3. minus unavailable overrides;
4. minus overlapping occupied `calendar_blocks`;
5. minus a fixed V1 `transition_buffer_minutes` of 15 minutes per occupied
   block only when the block splits an otherwise usable interval.

All values are clamped to `[0, D-now]`. Past intervals never contribute. A due
date without a time is normalized to the configured local end-of-day value
(`23:59`) and the normalization is stored/visible in the explanation.

If Nexus has **no enabled availability rule and no explicit availability
override**, capacity is `unknown`, not zero. The engine must not call work
`high` merely because it lacks time information.

V1 deliberately does not auto-write plans to Google Calendar. It may ingest
calendar blocks when a connector provides real start/end times; manual
availability is the supported fallback.

### 4.4 Action and feedback semantics

| User event | Required durable effect | Effect on next Today read |
|---|---|---|
| Accept | append `accepted` event with selected block length; retain obligation open | The item may remain recommended, labelled accepted; acceptance alone does not mark work complete. |
| Start | append `started`, set status `in_progress`, start a timer tied to the obligation ID | Today shows the running action and does not recommend a conflicting new start action. |
| Stop | append `stopped` with actual elapsed minutes; increment `spent_hours` | Remaining effort and risk recompute. No effort sample yet unless the user explicitly records one. |
| Complete | append `completed`; set status `completed`, `progress_percent=100`, `remaining_hours=0`; write effort sample only if measured/manual duration exists | Remove this obligation and recompute/rerank the next action. |
| Update progress/remaining | append `progress_changed`; update current values | Risk and recommendation recompute immediately. |
| Defer | append `deferred` with an explicit reason and optional next-review timestamp | Add a short execution-friction signal; do not falsify a deadline or auto-lower objective risk. |
| Dismiss a change | append/record acknowledgement against a `state_change` | Hides the notification but does not alter obligation state or risk. |

The legacy `completions.json` importer must create equivalent action events and
effort samples exactly once. Use an explicit `legacy_imported_at` migration
marker or event idempotency key; do not double-count time after restart.

---

## 5. Risk and recommendation contract

### 5.1 Risk engine inputs and output

Implement a pure function with an explicit input model, for example:

```python
evaluate_obligation_risk(obligation, capacity, now, effort_model) -> RiskAssessment
```

Its output must include:

- `band`: `overdue | high | medium | low | unscored`;
- `remaining_hours` and its basis (`manual`, `history`, `type_prior`);
- `available_hours` or `None` with reason `availability_unknown`;
- `load_ratio = remaining_hours / available_hours` when capacity is known;
- ordered, machine-readable `reasons` (for example `deadline_within_24h`,
  `remaining_exceeds_available`, `calendar_conflict`, `overdue`);
- `computed_at` and `state_revision`.

V1 uses the following deterministic bands:

1. `overdue`: obligation is active and `due_at < now`.
2. `high`: capacity is known and either `remaining_hours > available_hours`, or
   the due date is within 24 hours and `load_ratio >= 0.60`.
3. `medium`: capacity is known and either `load_ratio >= 0.50`, or deadline is
   within 72 hours and `remaining_hours > 0`.
4. `low`: capacity is known, deadline is within the planning horizon, and no
   higher band applies.
5. `unscored`: no deadline, no remaining estimate, or availability is unknown
   (unless it is already overdue).

These thresholds are V1 product constants placed in one named configuration
object and covered by tests. They are not user settings in the demo.

### 5.2 Ranking contract

Today ranks active obligations by this tuple, in order:

1. risk severity (`overdue`, `high`, `medium`, `low`, `unscored`);
2. positive overload gap (`remaining - available`) descending;
3. deadline ascending (undated last);
4. remaining hours descending;
5. stable obligation ID ascending.

Existing `Task.priority` must not appear in this tuple. This makes order stable,
explainable, and testable.

### 5.3 Recommendation contract

The top ranked eligible obligation receives one `Recommendation` containing:

- the stable `obligation_id` and title;
- an action verb: `start`, `continue`, `finish`, or `review_change`;
- a block duration: `min(remaining_minutes, available_contiguous_minutes, 50)`;
- a specific first step. V1 default: `Open <title> and work for <N> minutes`.
  If a manual next step exists, use it verbatim;
- the risk band and at least one factual reason;
- the remaining and available time values when known;
- a deterministic `recommendation_key` derived from state revision, obligation,
  and action parameters.

If an action is already running, Today first shows that action and its elapsed
time. It does not quietly switch the recommendation to a different obligation.
If no recommendation is safe, Today says precisely what input is missing (for
example, “Add when you can study this week to calculate risk”), with one link to
the relevant minimal setup field.

### 5.4 Required propagation examples

| Given | When | Then |
|---|---|---|
| An open assignment due in 7 days; remaining 6h; 12h known available | deadline becomes 24h away and only 2h remain available | same obligation ID persists; delta is `due_changed`; risk is `high`; it ranks above lower-risk work; Today explanation names 6h remaining vs 2h available. |
| Same assignment but no availability rules/overrides | it is recomputed | risk is `unscored`, not `high`; Today asks for availability rather than inventing certainty. |
| Two open obligations; the top one has been accepted and started | the user completes it after 90 measured minutes | it becomes `completed`, leaves active Today, a 90-minute effort sample is stored, and the second obligation becomes the recommendation after refresh. |
| A 3h assignment has 1h spent | student records 50% progress/2h remaining | state change persists, `remaining_hours` is 2h, and risk recomputes without waiting for sync. |
| The same work appears from iCal and manual/import sources | state ingest runs | one obligation has both source observations; Today shows one action, never duplicate actions. |

---

## 6. User flows

### Flow A: first useful Today without connectors

1. Student opens Nexus and sees an empty Today state.
2. They add one course, one assignment, its due date, estimated remaining time,
   and two availability windows through the thin setup form.
3. Canonical state is persisted. Today immediately calculates capacity, risk,
   and one start action.
4. Student can start and later complete exactly that obligation.
5. A reload preserves the state, feedback, and changed recommendation.

This is the mandatory demo path. It must work with network disabled and no
Google/Brightspace account.

### Flow B: changed deadline

1. An existing assignment is stored with a stable obligation ID.
2. A collector observation or manual edit changes its deadline.
3. State ingestion writes one `due_changed` event including before/after values.
4. Risk/recommendation recalculates in the same request/read cycle.
5. Today surfaces the revised action and explanation; acknowledgement of the
   change is separate from acknowledgement of the work itself.

### Flow C: action feedback changes the plan

1. Today recommends an obligation and explains it is high risk.
2. Student taps Accept, then Start on the same obligation.
3. The UI shows active elapsed time bound to that ID.
4. Student taps Complete, optionally supplying actual duration if no timer ran.
5. The item disappears from active state; an effort sample is written only for
   actual duration; the next action is re-ranked and survives reload.

### Flow D: calendar conflict reduces capacity

1. The student has recurring availability Friday 16:00–21:00.
2. A calendar block or manual unavailable override occupies 17:00–20:00.
3. Capacity before the relevant deadline falls from 5h to 2h.
4. The engine recomputes risk and Today reflects the changed factual reason.

### Flow E: incomplete state is honest

1. Student adds an obligation with a deadline but no availability data.
2. Today still presents the nearest obligation, but labels risk `unscored`.
3. It offers the one setup action needed to calculate risk.
4. It never calls the item high priority solely from a date.

---

## 7. Functional requirements and acceptance criteria

`FR-*` identifiers are the implementation checklist. Every item requires its
listed automated tests before it is marked complete.

### FR-1: canonical state

- **FR-1.1** Every manually added or collected academic obligation is upserted
  through one canonical state service into SQLite. `tasks.json` remains a
  replaceable collector cache and cannot be the only durable record.
- **FR-1.2** Identity remains stable when only `due_at` changes. A matching
  cross-source observation merges into the same obligation within the current
  normalized identity rules.
- **FR-1.3** The state service records a durable, queryable delta for create,
  deadline change, progress change, completion, disappearance, and calendar
  change.
- **FR-1.4** Legacy `tasks.json` and `completions.json` load without data loss;
  migration/import is idempotent.

**Acceptance:** Create an obligation, change its deadline, restart the process,
and query one row with the original ID, revised deadline, and one
`due_changed` delta. Importing the same legacy completion twice produces one
effort sample and one completed obligation state.

### FR-2: availability and calendar capacity

- **FR-2.1** Students can create, edit, and remove recurring availability
  windows and one-off availability/unavailability overrides in the V1 UI.
- **FR-2.2** A normalized calendar block has start and end times and subtracts
  from capacity exactly once, even after repeat sync.
- **FR-2.3** Capacity calculation correctly handles timezone conversion,
  overlapping windows, cross-midnight windows, expired windows, due-time
  clipping, and all-day/invalid events.
- **FR-2.4** Missing availability is explicitly `unknown`, never interpreted
  as zero available time.

**Acceptance:** With 12h available before a deadline, capacity reports 12h.
Adding a 3h overlapping calendar block reports 9h after reload. Removing the
last availability source returns `None/unknown`, not 0h.

### FR-3: risk

- **FR-3.1** The risk engine is deterministic, pure, and produces the required
  band, numerical inputs, and reason codes.
- **FR-3.2** Manual remaining effort overrides learned/type estimates; measured
  feedback updates effective remaining effort and later estimates according to
  the documented estimator.
- **FR-3.3** Risk does not read or use manual `priority`.
- **FR-3.4** An active overdue item is `overdue`; no non-overdue item is
  labelled `high` when availability is unknown.

**Acceptance:** Move a 6h unfinished task from seven days away to 24 hours
away with 2h capacity. It must become `high`; Today must rerank it first; all
three values must remain after a fresh process/database read.

### FR-4: recommendation

- **FR-4.1** Today calculates one top recommendation from the canonical state
  on every read; it never reads a stale recommendation as authoritative.
- **FR-4.2** The recommendation names the exact obligation, a block duration,
  a first step, risk band, and factual explanation.
- **FR-4.3** Tie-breaking follows the ranking tuple in section 5.2 and is
  stable across repeated reads.
- **FR-4.4** A running action takes precedence over a new recommendation.
- **FR-4.5** When state is insufficient, Today displays an honest missing-input
  explanation and a direct setup action, not generic study advice.

**Acceptance:** Given the same state and `now`, two engine calls serialize to
identical recommendation payloads. Given a running action, the Today payload
references that exact obligation regardless of another task's urgency.

### FR-5: feedback loop

- **FR-5.1** Accept, start, stop, complete, progress update, and defer create
  durable `action_events` tied to an obligation ID.
- **FR-5.2** Start/complete controls are rendered only from an obligation ID,
  never a collector task ID. The same rendered title and submitted ID must
  match.
- **FR-5.3** Completion removes work from active Today, persists status across
  sync/reload, sets remaining to zero, and immediately recalculates the next
  recommendation.
- **FR-5.4** Only measured or explicitly entered actual duration produces an
  effort sample. Completion alone must not infer duration from due date or
  creation time.
- **FR-5.5** A new historical sample changes later effective effort estimates
  according to the existing prior/shrinkage contract, with sample basis shown in
  state/recommendation payloads.

**Acceptance:** Start an assignment at 09:00, complete it at 10:30, reload,
and assert: one 90-minute sample; completed status; no active recommendation
for it; next action changed; a later same-kind task exposes a history-informed
estimate. Repeat completion must be idempotent.

### FR-6: Today UI

- **FR-6.1** The default page is named `Today`, not `Tonight`.
- **FR-6.2** Above the fold it shows only: current state freshness, one next
  action, why it matters, remaining/available time when known, and action
  controls. Secondary state is progressively disclosed.
- **FR-6.3** A minimal state-management surface permits manual obligation,
  availability, and progress input. It must not ask for manual priority.
- **FR-6.4** Change acknowledgement is visible only when a state delta needs
  attention and cannot replace/reorder an actual work recommendation.
- **FR-6.5** Existing Tasks/course/schedule modules may remain temporarily
  behind a secondary “State” surface, but no new dashboard cards or tabs are
  added to V1.

**Acceptance:** A Streamlit UI test renders Today from the deterministic V1
demo and finds one primary action, its exact title/ID/reasons, available-time
copy, and Start/Complete control. It does not render a priority picker.

### FR-7: demo and operability

- **FR-7.1** `nexus demo` (or a clearly named `--scenario v1-loop` mode)
  creates an empty-directory, account-free scenario that includes course,
  obligations, availability, calendar conflict, deadline delta, and feedback
  samples.
- **FR-7.2** A machine-readable demo/CLI output exposes canonical obligations,
  risk assessments, top recommendation, and action-event count without course
  content or credentials.
- **FR-7.3** Existing local-first security, backup, locking, `doctor`, sync,
  and clean-room behavior remain valid.

**Acceptance:** The scripted demo completes the deadline-change and
complete-action scenarios with network disabled, exits 0, and every assertion
in the V1 acceptance suite passes.

---

## 8. Implementation phases

### Phase 0 — Freeze the contract and create acceptance fixtures

**Files:** this plan, `tests/fixtures/`, new V1 tests only.

1. Add deterministic time-based fixtures for: base state, deadline moved to 24
   hours, calendar conflict, no-availability degradation, action completion,
   and cross-source duplicate.
2. Add a `V1_NOW` fixed timestamp used by all V1 unit/integration tests.
3. Write failing acceptance tests before changing production code.

**Exit criterion:** New tests express all rows in section 5.4 and FR-1–FR-7;
the baseline suite still passes except for consciously introduced failing V1
tests.

### Phase 1 — Make the academic state durable and singular

**Likely files:** `src/nexus/academic_state.py`, `src/nexus/task_service.py`,
`src/nexus/knowledge/schema.sql`, `src/nexus/knowledge/sqlite_store.py`, a new
`src/nexus/academic_state_service.py` (or a carefully scoped extension),
`src/nexus/models.py`.

1. Add schema migrations for the entities in section 4, using the existing
   migration framework and no destructive table reset.
2. Introduce obligation-based service APIs for manual add/edit, ingestion,
   progress update, and state delta listing.
3. Persist source provenance transactionally with obligation upserts.
4. Make `tasks_from_obligations()` a compatibility projection only; do not let
   it create independent state.
5. Import legacy completion entries once and add a backward-compatible adapter
   for callers until Phase 3 replaces them.

**Exit criterion:** Identity/delta/restart/migration acceptance tests pass;
`tasks.json` can be deleted after ingestion and canonical open obligations still
produce a valid state read.

### Phase 2 — Add capacity and availability-aware risk

**Likely files:** new `src/nexus/availability.py`, new
`src/nexus/intelligence/risk.py`, `src/nexus/intelligence/workload.py`,
`academic_state.py`, schema/store tests.

1. Model recurring availability, overrides, and duration-bearing calendar
   blocks.
2. Implement a pure capacity calculator with explicit timezone input.
3. Implement `RiskAssessment` and the exact thresholds/reasons in section 5.
4. Retain current effort estimator as the first effort model, but expose the
   actual estimate basis and do not label risk high when capacity is unknown.
5. Backfill/update `obligations.risk_band` only as a cache after calculation;
   never use it as the sole input on a subsequent read.

**Exit criterion:** Deadline-change, conflict, unavailable/no-availability,
timezone, and priority-independence tests pass. Existing workload tests are
adapted only where their old deadline-first contract conflicts with the explicit
V1 contract.

### Phase 3 — Recommendation and feedback loop

**Likely files:** new `src/nexus/recommendations.py`, new
`src/nexus/action_service.py`, `task_service.py`, `tonight_plan.py`,
`intelligence/workload.py`, schema/store tests.

1. Define the serializable `Recommendation` model and pure selection function.
2. Introduce one action-event write path keyed by obligation ID.
3. Migrate existing start/complete functions and plan acceptance to call that
   path. Do not retain two parallel timers or completion records.
4. Implement idempotency safeguards for repeated button submissions/reloads.
5. Recompute recommendation after every event and expose measured feedback to
   effort estimation.
6. Retire `judgment.json` and `tonight_plan.json` as authoritative state after
   their callers use the new service. A read-only importer/adapter may remain
   for one beta cycle if tests require it.

**Exit criterion:** The complete start → 90-minute completion → next action
sequence passes against a fresh SQLite database and after an application
restart. Every action refers to the same obligation ID displayed in Today.

### Phase 4 — Compose the thin Today surface

**Likely files:** `src/nexus/dashboard/app.py`,
`src/nexus/dashboard/view_model.py`, `src/nexus/dashboard/styles.py`, dashboard
smoke tests.

1. Replace default `Tonight` tab with `Today` and drive it from one dedicated
   Today view model, not a mix of tasks, judgment, and plan JSON files.
2. Render the primary action above the fold; retain State/Setup only for manual
   obligations, availability, progress, source health, and secondary browsing.
3. Remove/hide priority input from the manual form; collect estimated or
   remaining hours and availability instead.
4. Bind action buttons to `obligation_id`, call `ActionService`, then rerun and
   rebuild the view model from SQLite.
5. Keep the existing dashboard smoke behavior where compatible, but delete
   obsolete UI paths only after their replacements have E2E coverage.

**Exit criterion:** The UI acceptance test completes the V1 demo flow without
mocking decision logic, and a manual visual check confirms one obvious primary
action rather than a dashboard of competing cards.

### Phase 5 — Demo, release rehearsal, and user-test instrumentation

**Likely files:** `src/nexus/demo.py`, `src/nexus/cli/demo.py`,
`docs/PRIVATE_BETA.md`, `docs/TESTING.md`, tests.

1. Make the demo run all core transitions deterministically and print a compact
   JSON proof.
2. Add a `make v1-acceptance` target that executes the V1 test group plus the
   account-free CLI scenario.
3. Update beta instructions to ask testers about state credibility,
   recommendation usefulness, and whether completing work changed tomorrow’s
   result—not whether a dashboard looked attractive.
4. Run the existing release checks after V1 acceptance passes.

**Exit criterion:** A clean checkout performs the account-free V1 loop,
`make test`, V1 acceptance, `make release-smoke`, and relevant wheel smoke
checks pass; a human can follow the demo in under five minutes.

---

## 9. Test strategy

### 9.1 Required test layers

| Layer | Purpose | Required examples |
|---|---|---|
| Unit | Pure identity, capacity, risk, ranking, estimator, serializers | 24h deadline jump; unknown availability; overlapping blocks; exact tie break; priority ignored. |
| SQLite integration | Migrations and transactionally persisted state | stable ID across restart; idempotent legacy import; source merge; action event and completed state in one transaction. |
| Service integration | Full state/delta/risk/recommendation/action transitions | change deadline → risk/rank changes; start/complete → effort sample/next action. |
| UI/E2E | Rendering and button wiring against real service logic | Today shows one action; ID/title match; action changes the rerendered recommendation; no priority field. |
| CLI/demo | Account-free, deterministic release proof | demo emits valid state/risk/recommendation/action summary and scenario assertions. |
| Regression | Existing connectors, knowledge, release, privacy, concurrency | full existing suite and targeted sync tests remain green. |

### 9.2 Proposed test files

| New/updated file | Primary contract |
|---|---|
| `tests/test_availability.py` | interval normalization, capacity, calendar conflict, unknown availability |
| `tests/test_risk.py` | bands, explanations, thresholds, priority independence |
| `tests/test_recommendations.py` | ranking, deterministic key, running-action rule, missing-input copy model |
| `tests/test_action_service.py` | accept/start/stop/complete/defer, idempotency, effort-sample rules |
| `tests/test_v1_state_loop.py` | durable State → Delta → Risk → Recommendation → Action → Feedback path |
| `tests/test_v1_migrations.py` | fresh and legacy runtime migrations without loss/double import |
| `tests/test_dashboard_today.py` | Streamlit Today default and service-bound button behavior |
| `tests/test_demo.py` | extend current demo with V1 scenario assertions |
| `tests/test_academic_state.py` | preserve/add identity and source-provenance cases |
| `tests/test_task_service.py` | migrate/remove source-task completion assumptions only after compatibility tests pass |

### 9.3 Core acceptance test (mandatory)

Implement one named, end-to-end integration test equivalent to this scenario:

```text
Given fixed now = Monday 2026-09-07 09:00 local time
And “CS Assignment 2” is open, has 6h remaining, and is due next Monday
And availability before its deadline totals 12h
And “History Reading” is open, has 1h remaining, and is due Wednesday
When CS Assignment 2 is changed to due Tuesday 09:00
And a calendar block leaves only 2h of available study time before Tuesday
Then CS Assignment 2 keeps the same obligation ID
And exactly one due_changed state delta is recorded
And its risk is high with reason remaining_exceeds_available
And Today ranks it first and recommends a concrete CS Assignment 2 block
When the student accepts, starts, and completes it after 90 measured minutes
Then completion, effort sample, and action events survive a new process read
And CS Assignment 2 is absent from active Today
And History Reading is now the top recommendation
```

This test must use real SQLite and the real risk/recommendation/action services;
it may use synthetic collectors and a fixed clock. It is the release gate for
the product thesis.

### 9.4 Commands required at each phase

```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m pytest tests/test_v1_state_loop.py -q
PYTHONPATH=src python -m pytest tests/test_risk.py tests/test_action_service.py -q
PYTHONPATH=src python -m nexus.cli.main demo --data-dir /tmp/nexus-v1-demo --json
make release-smoke
```

After Phase 5, add and run:

```bash
make v1-acceptance
```

The target must create its own temporary directory and never use or overwrite a
student’s real `data_dir`.

---

## 10. Non-functional requirements

### Correctness and resilience

- All state/action writes are atomic under the repository’s existing local lock
  and SQLite transaction patterns.
- Repeated Sync, repeated UI rerun, and double-clicked action buttons are
  idempotent. They do not duplicate deltas, events, effort samples, or calendar
  capacity subtraction.
- A failed optional collector leaves last known canonical state readable and
  visibly marks source freshness; it does not delete obligations.
- Every risk explanation is reproducible from persisted state, `now`, and V1
  constants.

### Performance

- Today calculation must require no network or LLM call.
- For a local runtime with 500 obligations and 1,000 calendar blocks, a warm
  Today state calculation should complete within 500 ms on a normal developer
  laptop. Add a focused benchmark only if this target is missed.
- Queries must be indexed by active obligation status/due date and calendar
  block interval; avoid loading unrelated knowledge/Chroma content.

### Privacy and safety

- Preserve local-first data storage and owner-only runtime permissions.
- Do not log raw calendar titles, course content, feed URLs, OAuth tokens, or
  notes in error messages or machine-readable acceptance output.
- Manual state edits remain reversible/auditable through state changes or a
  safe edit history; no destructive “reset” is needed for ordinary corrections.

### Explainability and accessibility

- Every displayed high/medium/overdue recommendation states why in plain
  language and shows its factual inputs where available.
- Today must have a complete no-data state and a complete unknown-capacity
  state. Empty states are product states, not errors.
- UI controls have text labels; color is never the only risk signal.

---

## 11. Definition of done

V1 is done only when all of the following are true:

- [ ] Every FR-1 through FR-7 acceptance criterion passes in automation.
- [ ] The mandatory core acceptance test in section 9.3 passes on a fresh
      temporary runtime and after a restart.
- [ ] `python -m pytest -q` passes with all existing and V1 tests.
- [ ] `make v1-acceptance` and `make release-smoke` pass from a clean checkout.
- [ ] The account-free demo proves state creation, deadline delta, capacity
      conflict, risk reranking, one action, completion feedback, and reranking.
- [ ] Today is the default surface and contains one clear next action. The user
      does not enter priority to make the system useful.
- [ ] A manual tester can complete Flow A, Flow B, and Flow C with network
      disabled, then confirm persistence after relaunch.
- [ ] Existing sync, privacy, migration, and packaging contracts are unchanged
      unless their tests/docs are deliberately updated in the same change.
- [ ] The implementation includes no unreconciled duplicate write paths for
      completion, plan acceptance, or risk calculation.
- [ ] Documentation tells beta testers how to evaluate the loop and explicitly
      states the inputs Nexus cannot yet infer.

“Code written,” “page renders,” “happy-path demo shown once,” and “the model
looks intelligent” are not completion conditions.

---

## 12. Explicit non-goals

The following are out of V1 scope unless needed to protect the loop above:

- AI chat, conversational tutoring, and LLM-generated risk decisions;
- notes, flashcards, Pomodoro, generic task management, or a general calendar;
- automated Gmail/Canvas/Albert/syllabus coverage beyond currently supported
  connectors and their safe normalization paths;
- automatic calendar write-back, automatic schedule rearrangement, reminders,
  mobile apps, multi-user/cloud hosting, billing, or analytics collection;
- a broad dashboard redesign, data visualizations, charts, or course analytics;
- personalized ML beyond the current transparent type prior + measured-history
  effort estimator;
- grade prediction, clinical/mental-health claims, or guarantees that a student
  will meet a deadline;
- redesigning the knowledge/QA/memory subsystem unless a small compatibility
  change is necessary for existing tests to keep passing.

---

## 13. Agent execution rules

1. Begin each phase by running its targeted tests and recording the baseline.
2. Read the current code before replacing an existing path; prefer adapting the
   tested obligation/SQLite foundation to duplicating it.
3. Make the smallest vertical slice possible: schema/service/test first, then
   UI. Do not land UI mock state that bypasses the service layer.
4. For every requirement, prove: input enters state → delta is persisted → risk
   changes → Today changes → action writes feedback → next read changes.
5. When a test fails, diagnose the contract violation, fix it, and rerun the
   focused test plus relevant regression tests. Do not weaken an acceptance test
   merely to make the suite green.
6. Treat unavailable calendar capacity as unknown. Do not substitute a guessed
   high-risk label.
7. Keep LLM calls, connector calls, and sync execution out of Today rendering
   and pure decision tests.
8. Stop for a product decision only if it contradicts a rule in section 1.3 or
   requires scope beyond section 12. Otherwise choose the smallest behavior
   consistent with this document and verify it.

This plan deliberately uses the current repository’s strongest assets—stable
obligations, delta logging, local persistence, timed completion, effort
history, and a tested synthetic runtime—to release a real loop before expanding
state coverage or interface breadth.
