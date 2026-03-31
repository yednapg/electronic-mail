# V1 Implementation Plan: Strict Decision Pipeline for Gmail + Calendar

## Summary
Build an invite-only, web-first alpha for Gmail + Google Calendar users that shows only decision-worthy items on the main dashboard and keeps awareness off-main by default.

Main dashboard only:
- `Day Brief`
- `Needs Action Now`
- `Due Today`

Off-main:
- collapsed `Worth Knowing`
- secondary inbox/history view
- operator trace console

V1 principles:
- one entity, one lifecycle
- one decision pipeline
- no duplicate surfacing
- no expired items
- every main-surface item has a safe, clear next step
- every source record is fully traceable

## Core Rules
### Canonical model
- `need_type`: `decision | awareness`
- `action_type`: `inline | external | none`
- `effort_level`: `quick | deep`
- `timing_band`: `now | today | later | hidden`
- `action_confidence`: `high | medium | low`

The system must never output `review` as a type.

### Importance heuristic
Use a simple v1 heuristic. `importance = high` if any of these are true:
- deadline exists
- sender is human, not system/promotional
- thread has reply expectation
- financial impact exists
- calendar impact exists

### Clear action rule
A `clear action` exists if:
- the user can act in `<= 1` step inside the app, such as reply, confirm, mark done
- or a single external link is the true action target, such as pay bill, open GitHub PR, open doc

If neither condition is true, the action is not clear.

### Atomic drop and downgrade rules
```text
IF need_type != decision
  -> hidden (default)

IF need_type == decision AND no clear action:
  IF importance == high
    -> action_type = external, primary_action = open
  ELSE
    -> hidden

IF action_type == inline AND action_confidence != high
  -> downgrade to external
```

### One entity, one lifecycle
Each entity can only be in one state:
- `active`
- `scheduled`
- `resolved`
- `suppressed`

Meanings:
- `active`: visible in `Now` or `Today`
- `scheduled`: hidden until resurfacing time
- `resolved`: completed, hidden
- `suppressed`: hidden due to awareness, noise, duplicate merge, or low-value no-action

### Completion rule
In v1:
- when a user completes an action, mark the relevant email as read
- full archive behavior is deferred
- completed entities leave `active`

## Pipeline Order
The pipeline must run in this exact order:
1. ingest source record
2. normalize
3. group into entity
4. derive entity state
5. determine `need_type`
6. determine whether a clear action exists
7. assign `action_type` and `effort_level`
8. assign `timing_band`
9. apply drop/downgrade rules
10. rank within band
11. place into section or suppress
12. persist lifecycle state and trace

Timing and ranking must remain separate:
- timing chooses the band
- ranking only orders items inside the band

## Timing and Surface Rules
### Timing bands
- `NOW`
- deadline < 24h
- blocking thread
- explicit urgency

- `TODAY`
- deadline within 1-3 days
- explicit user resurfacing for today
- important but not immediate
- borderline high-importance uncertain items

- `LATER`
- decision exists but should not be on main surface yet

- `HIDDEN`
- awareness
- noise
- duplicate/merged record
- expired/resolved
- low-value no-action item

### Miss protection
Prefer false positive over miss:
- never hide high-importance uncertain items
- if uncertain but important, place in `Today`
- uncertainty may downgrade action quality, but must not silently bury important entities

### Visibility policy
V1 should be deterministic:
- main surface stays bounded and lean
- extra valid items go to overflow/collapsed or secondary views
- no item may appear in more than one section

## Entity Identity
Use:
- `entity_id = thread_id` for simple cases
- `entity_id = normalized_group_id` when multiple emails represent one underlying thing

Examples:
- registration email + RSVP email + ticket email -> one entity
- repeated bill reminders -> one entity

## Runtime Shape
Keep only three parts:
1. `sync layer`
2. `decision pipeline`
3. `app layer`

### Responsibilities
- TypeScript: sync, orchestration, lifecycle, APIs, UI contracts, actions, traces
- Python: structured decision step only

Do not split timing, learning, feed, or ranking into separate runtime services in v1.

### Learning in v1
Capture signals only:
- explicit feedback
- open, dismiss, snooze, act, ignore
- replay outcomes

No runtime self-tuning in v1.

## Traceability
Every source record must be traceable through:
- ingestion
- normalization
- grouping
- state derivation
- decision classification
- action selection
- timing assignment
- ranking
- surfacing or suppression
- lifecycle transition
- user outcome

Each visible item should expose:
- `why this is here`
- `trace_id`

The operator console should support replay from raw source record to final outcome.

## Implementation Phases
Implement in narrow, reviewable slices. Do not jump ahead.

### Phase 1: Foundation
- initialize monorepo/app structure for web app, TS backend, and Python decision component
- define core contracts: `SourceRecord`, `Entity`, `AttentionItem`, `PipelineOutput`, `TraceRecord`, `FeedbackEvent`
- define lifecycle, timing, and drop-rule enums/constants
- set up Postgres schema and migration baseline
- set up test harnesses for TS and Python
- set up trace-first logging and error handling

Review checkpoint:
- schema, contracts, and state machine reviewed before any Gmail sync logic

### Phase 2: Sync Layer
- Google OAuth
- Gmail sync and raw record persistence
- Google Calendar sync and raw record persistence
- webhook/polling baseline
- sync checkpoints, retries, and idempotency

Review checkpoint:
- inspect exact stored source shape, retry semantics, and trace entries

### Phase 3: Entity Pipeline
- normalization
- thread/entity grouping
- normalized group id generation
- lifecycle transitions
- state derivation
- expired/resolved suppression

Review checkpoint:
- verify one entity per real-world thing
- verify no duplicate surfacing paths exist

### Phase 4: Decision Step
- implement `importance` heuristic
- implement `clear action` check
- implement `need_type`, `action_type`, `effort_level`
- enforce downgrade/drop rules
- implement typed Python decision output
- TS validates and persists output

Review checkpoint:
- inspect real examples: newsletter, RSVP, bill, PR, ticket

### Phase 5: Timing and Ranking
- implement `now`, `today`, `later`, `hidden`
- ranking within band only
- miss-protection defaults
- bounded main-surface assembly

Review checkpoint:
- verify timing examples against your edge cases before UI work expands

### Phase 6: Main Product UI
- Day Brief
- Needs Action Now
- Due Today
- collapsed Worth Knowing
- secondary inbox/history view
- item detail with `why this is here`
- safe action surface with one primary action and open fallback

Review checkpoint:
- UI reviewed against real user flows before adding more actions

### Phase 7: Core Actions
- mark email as read on completed action
- inline reply when confidence is high
- external open actions
- snooze/show-later
- RSVP where safe

Review checkpoint:
- action safety and side effects reviewed in detail before broader rollout

### Phase 8: Operator Console
- per-item trace view
- pipeline-stage breakdown
- replay endpoint and UI
- failure categorization
- surfaced vs suppressed inspection

Review checkpoint:
- validate that every wrong output is diagnosable without guesswork

### Phase 9: Alpha Hardening
- replay corpus
- regression tests
- rate-limit and retry hardening
- invite-only onboarding
- manual end-user test checklist

Review checkpoint:
- no alpha users until replay and manual daily checks are stable

## Test Plan
### Unit tests
- normalized group id generation
- one-entity dedupe behavior
- lifecycle transitions
- importance heuristic
- clear-action detection
- atomic drop/downgrade rules
- inline confidence guardrail
- timing assignment
- no cross-section duplication
- resolved suppression

### Integration tests
- old bill email resurfaces correctly
- event emails collapse into one entity
- ticket moves off-main after RSVP
- low-confidence inline action downgrades to external
- completed action marks email as read
- every ingested record has final traceable state

### Replay scenarios
- newsletter hidden during busy period
- urgent item shown before damage
- high-importance uncertain item lands in `Today`
- duplicate reminders do not create duplicate cards
- awareness appears only in `Worth Knowing`
- resolved item does not reappear without new state

## Assumptions
- audience: high-agency Gmail/GCal users
- auth: Google OAuth only
- product boundary: light replacement for core daily work, not full Gmail parity
- awareness: off-main by default
- off-main label: `Worth Knowing`
- completion in v1: mark read, not full archive
- busy detection: calendar + manual focus state
- storage: full history for replay/debugging
- stack: TypeScript runtime with Python decision step
