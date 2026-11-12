# One-click Bot Control Center — complete Codex implementation plan

## 0. Instruction to Codex

Use this document as the authoritative specification for the local installation at `D:\telegram bots`. It consolidates the original requirements, organizational changes, persistent project instructions, two operational corrections, and pre-mortem safeguards. Earlier drafts and addenda are background evidence; their operative requirements are incorporated here.

Inspect the actual installation, establish the canonical plan at `docs/IMPLEMENTATION_PLAN.md`, and execute the seven phases in Section 11. Maintain `docs/PROJECT_STATUS.md` and the acceptance evidence as work progresses. Make reasonable implementation choices within these boundaries and continue useful independent work when a particular check is blocked. Do not repeatedly request permission for already-authorized, in-scope work.

This document is a handoff specification. Preparing it did not implement, migrate, delete, or run anything on the Windows installation. Code, commands, skills, and target paths described below are planned until implementation and verification establish otherwise.

The initial goal is a reliable local home for two independent applications—Home and French—with a consistent way to add future bots. Preserve existing business behavior and data. Include only minimal, justified compatibility or persistence fixes needed to meet these requirements. Keep new bot business features, runtime AI coordination, additional management servers, remote hosting, and unrelated system changes outside this version.

Controlled removal of proven obsolete material is authorized under Section 9. This replaces earlier blanket no-deletion/indefinite-retention clauses. Protection of live data, approved content, unique source/Git work, and required recovery evidence remains mandatory.

## 1. Starting evidence and first inventory

### 1.1 Recorded observations, requiring refresh

Earlier checks reported:

- Home had a healthy SQLite database with 38 tasks and two members.
- Fourteen French note files matched the approved source and saved snapshot.
- Focused offline checks passed: 59 Home checks and 61 French checks.
- French source paths and shortcuts still referred to earlier locations.
- French reader state was under `%LOCALAPPDATA%\French Notes Telegram`; editor state was under `%LOCALAPPDATA%\French Notes\afc704eab9aa`.
- The large clutter areas were French `checks/` and Home `.local-telegram/`, including 27 direct Home `run-*` directories in the later inventory.
- Some tests actively consumed historical fixtures/markers or wrote to old output locations.
- The release copy at `Our_Home_Telegram_Bot/.publish/home_shopping/` contained its own Git repository, staged/uncommitted work, ignored material, and dangling objects. Earlier inspection described an unborn branch without local commit history.
- An earlier planning session reported Codex CLI `0.164.0-alpha.11.1`, enabled subagents, and no workspace-root Git repository, root instructions, or local skills.
- The supplied 5,171-line directory listing was read during planning; its location later changed.

These are historical observations, not fresh acceptance results. Refresh relevant state, paths, repository boundaries, installed tools, and fingerprints before mutation. Do not hard-code historical task/member counts as the final baseline or overwrite legitimate subsequent user changes to reproduce them.

No required original application files appeared missing in the initial check. Do not recreate missing-looking content without investigating its actual location. Creating the new core, instructions, templates, and maintained tools required by this specification remains part of the work.

### 1.2 Inventory requirements

Identify actual filesystem types, resolved links/junctions, active writers, external AppData/browser state, launchers, shortcuts, startup triggers, dependencies, repositories, and available space.

Create an exact old-to-new disposition ledger covering source, authoritative state, maintained tools, fixtures, generated outputs, retained evidence, and unresolved items. Check actual consumers, including dynamic loading, subprocesses, UI assets, manual commands, and release tooling.

Protect secrets before the first snapshot or Git staging operation. Capture credential-containing originals in protected encrypted recovery material; do not place plaintext tokens into ordinary archives, reports, or source history. Preserve the full nested repository—including its index, working tree, ignored files, and objects—before consolidation. A commit-only backup is insufficient for its recorded state.

Stop the affected mutation when new evidence contradicts the inventory. Preserve the changed material and identify the discrepancy; continue unrelated safe work where possible.

## 2. Platform, everyday behavior, and ownership

### 2.1 Platform and dashboard

Use:

- Python 3.11.
- FastAPI/Uvicorn for one persistent core server, with exactly one worker, reload disabled, and binding only to loopback.
- SQLite for core state and the existing application stores where applicable.
- Locally bundled HTML/CSS/JavaScript; no production dependency on a Node development server or remote CDN.
- A responsive layout with visible navigation, labeled controls, keyboard access, visible focus, and clear pending/error states.

The dashboard provides:

- Per-bot status, connection state, uptime, latest error, and rotating logs.
- Start, Stop, and Restart for each bot.
- Start all and Stop all.
- Add bot for creation or existing-project registration.
- Open French editor, without opening it automatically.
- Manual verified backup operations and recovery instructions.
- Quit core.

Show actual observed state and operation progress. Distinguish a submitted request, durably Accepted operation, and Completed operation. Do not report a successful start merely because process creation was requested.

Root `START.vbs` and `START.cmd` use one startup implementation. Repeated clicks reopen the authenticated dashboard without duplicate cores/bots or changes to saved intent. Closing the browser leaves the core and managed bots running. Reopening the core reconciles enabled bots against saved intent while preserving deliberate stops and external-instance holds.

Create no new Windows autostart entries. Retire only inventoried Home/French-owned startup triggers where needed to eliminate old entry points, saving their definitions for rollback. Leave unrelated startup entries and Windows power settings unchanged.

The single-server requirement applies to the core. Preserve the French editor's existing runtime with an independent lifetime; its operation must not depend on keeping the core or reader alive.

### 2.2 Responsibility model

| Responsibility | Owner |
|---|---|
| Requests and intended operation | User through dashboard and supported management commands |
| Validation, ordering, intent, operation records | Core command/lifecycle service |
| Managed process trees, health, shutdown, bounded recovery | Supervisor |
| Due occurrences, claims, skips | Scheduler, submitting through the command service |
| Declarations, identities, capabilities, canonical paths | Registry/configuration resolver |
| Reservations, capture, restore, verification | Recovery/maintenance coordination |
| Home task/member/panel semantics | Home package and validators |
| French notes/editor/reader/history/audio semantics | French package and validators |
| Reusable contracts and Windows primitives | Shared runtime |
| Verification selection and evidence | Test coordinator and suite owners |

Implement focused modules, not additional management processes or empty hierarchies. The scheduler never launches processes directly. The supervisor contains no application business rules. Workers may reconnect networking but must not implement a competing process-restart policy.

### 2.3 Process lifetimes

| Mode | Lifetime and ownership |
|---|---|
| Browser | Client only; closing it changes no intended state |
| Management CLI | Uses protected IPC; may inspect locally or explicitly start/connect to the persistent core |
| Persistent core | Owns managed processes, process handles, Job Objects, and restart policy |
| Genuine standalone runner | Remains alive for its bot's lifetime and owns its locks/worker containment; has no restart supervisor |
| Finite maintenance worker | Lives until its declared operation completes or is cancelled |
| French editor | Independent single-instance application; remains usable after reader/core lifecycle operations |

A finite CLI exits only after durable submission acknowledgment or completion of its finite operation. It never exits while holding the sole kill-on-close resources for a long-running bot.

Standalone mode uses canonical configuration, the same identity/state safeguards, and the same managed-contract primitives where applicable. It does not require a running dashboard or import FastAPI/scheduling merely to resolve paths or acquire locks. It does not silently rewrite core intent, enabled state, or attention.

When a standalone instance exists, the core reports **Running outside core**, prevents duplicate polling, and does not take it over. After it exits, require an explicit management action before assuming control. Persist this hold so a core restart cannot bypass it.

Reader Stop/Restart, Quit core, core crash, and essential-core-task failure must leave the editor usable. Keep it outside the managed trees terminated by those paths. Repeated Open French editor reuses its existing session.

## 3. Structure, code ownership, and packaging

### 3.1 Target layout

All paths in this document are relative to `D:\telegram bots` unless stated otherwise.

| Path | Purpose |
|---|---|
| `START.vbs`, `START.cmd` | One-click launch and fallback |
| `README.md` | Setup, everyday use, navigation, known operating limits |
| `AGENTS.md` | Concise root working instructions |
| `.agents/skills/` | Reusable development workflows |
| `core/` | Core API/UI, command service, supervisor, scheduler, registry, recovery |
| `shared/src/bot_center_runtime/` | Installable shared contracts and primitives |
| `bots/home/src/home_bot/` | Home application code and package assets |
| `bots/french/src/french_notes/` | French application code and package assets |
| `config/` | Nonsecret registry declarations, schemas, policies, settings |
| `launchers/home/`, `launchers/french/` | Supported standalone/compatibility entry points |
| `tools/` | Maintained auditing, migration, simulator, release, environment, testing, onboarding, cleanup tools |
| `tests/` | Core, shared, contracts, Home, French, integration, support |
| `docs/` | Canonical plan, project map/status, decisions, runbooks |
| `data/<component>/` | Authoritative mutable state/content, required caches, exports, histories |
| `logs/<component>/` | Rotating operational diagnostics |
| `backups/` | Verified recovery sets and their retained dependencies |
| `archive/` | Selected historical evidence and justified inactive originals |
| `work/` | Isolated tests, builds, staging, migration/validation output |
| `templates/python_bot/` | Single future-bot template source |
| `.venvs/` | Separate final-path Python environments |
| `.secrets/` | Current-user Windows-encrypted credentials with private permissions |

Create subdivisions only when an identified responsibility populates them. Root Node metadata/lockfile, when needed, supports browser QA only. Keep live state and generated output out of application source directories.

### 3.2 Existing material and disposition

| Existing material | Target responsibility |
|---|---|
| Home `bot.py`, `storage.py`, seed assets | Home package; preserve persistence/public behavior |
| Home `local_telegram.py` and simulator HTML | Maintained Home simulator tooling, without production credential loading |
| Home business tests | `tests/home/` |
| Home browser/simulation/performance harnesses | Catalogued tools/suites, retaining unique assertions |
| French `app.py` and `editor_store.py` | French editor module |
| French `store.py`, `tense_notes.py`, shared document parsing | French notes/document modules independent of the editor UI |
| French `telegram_bot/` and approved-source metadata | French Telegram package and application-owned package data |
| French speech service/workers/cache semantics | French audio subsystem |
| French Python/CJS tests and audio fixtures | `tests/french/` and owned support fixtures |
| Reusable audit, preview, playback, Windows, verification, release helpers | Maintained tools |
| Date-specific note-editing scripts/data | Historical migration evidence; do not run merely for reorganization |
| Consumed inputs under old output directories | Extract required fixtures/private corpus packs before cleanup |
| Repeated preservation/suite wrappers | One maintained implementation after distinct scenarios are accounted for |
| Unique restore/failure scenarios | Named maintained regressions |
| Nested release checkout | Protected complete snapshot, then evidence-based consolidation |
| Obsolete markers, wrappers, copies, environments | Removal only after Section 9 gates |

Use active application source as the migration starting point. Review differing release copies; do not overwrite active source with them. Similar names do not establish common responsibilities: inspect stores, guards, hashing, and atomic-write behavior before merging them.

Use descriptive English internal names such as `request_bot_shutdown`, `load_reader_preferences`, `canonical_history_path`, and `retry_delay_seconds`. Preserve approved note names/content/order, persistent keys and identifiers, callbacks, public CLI options, ownership, and audio references. Do not cosmetically rename approved note filenames, including existing spelling or accents.

A necessary public/persistent change needs an explicit compatible migration and focused evidence. Preserve compatibility wrappers until their actual consumers migrate.

### 3.3 Shared code and environments

Shared runtime owns genuinely reused path/configuration contracts, protocol types, managed-client hooks, redaction, resource-lock interfaces, and Windows process/pipe/credential primitives.

Dependency direction is core/applications → shared runtime. Shared imports neither core nor applications; Home and French import neither each other. Domain schemas, note validation, accepted snapshots, history semantics, audio cache keys, and business callbacks remain application-owned.

Use `pyproject.toml`, importable packages, documented module/console entry points, packaged assets, and installed-build verification. Build versioned wheels; record source/build identities, hashes, dependency locks, and installation reports. Install the shared artifact separately into each consumer environment. Production uses no editable installs or ad hoc `sys.path`/`PYTHONPATH` dependencies. [S1–S2]

Recreate environments at final paths rather than moving old virtual environments. Initially preserve dependency versions and investigate local/path installs, modified packages, system executables, and browser/audio prerequisites before declaring reproduction verified.

Use separate core/QA, Home, French reader, French editor, and browser-tool environments where required by their existing dependencies. Do not force incompatible stacks together. Keep compatible build/reconstruction inputs needed by retained recovery sets.

## 4. Instructions, durable context, and development skills

### 4.1 Instruction and documentation ownership

| File | Purpose | Initial phase |
|---|---|---|
| Root `AGENTS.md` | Purpose, boundaries, authorities, preservation, navigation, resume/finish rules | 1 |
| `core/AGENTS.md` | Intent, commands, supervision, security, schedules, recovery | 1 |
| `shared/AGENTS.md` | Contracts, dependency direction, compatibility, consumer checks | 1 |
| `bots/home/AGENTS.md` | Home invariants and checks | 1 |
| `bots/french/AGENTS.md` | French content/state/audio invariants and checks | 1 |
| `tests/AGENTS.md` | Isolation, resources, evidence, test selection | 1 |
| `tools/AGENTS.md` | Execution scope, resources, outputs, cleanup | 1 |
| `docs/IMPLEMENTATION_PLAN.md` | This canonical specification and acceptance map | 1 |
| `docs/PROJECT_MAP.md` | Task-oriented locations, owners, dependencies, commands, state | 1; maintained |
| `docs/PROJECT_STATUS.md` | Sole current development checkpoint/handoff | 1; maintained |
| `docs/DECISIONS.md` | Material decisions, reasons, affected contracts, supersession | 1; maintained |
| `docs/ADDING_A_BOT.md` | Actual supported creation/registration procedure | Scaffold 1; complete 7 |
| Development, testing, cleanup, operations, recovery runbooks | Detailed procedures linked to authoritative tools/configuration | Incremental |

Reuse equivalent existing records rather than duplicating them. Preserve existing instruction bytes before changing them. Keep useful French rules; explicitly supersede obsolete paths, output routing, and blanket-retention rules.

Root guidance directs contributors to applicable scoped instructions before edits. Keep rules concise and link to detailed procedures. Do not copy the entire specification into every component.

### 4.2 Local history and discovery

After protecting the nested repository, establish a local workspace-root Git repository for source/context history, with private/generated exclusions prepared before staging. Use the existing configured identity; change no global settings, add no remote, and publish nothing. If identity configuration is unavailable, report that specific limitation without inventing an identity or blocking independent work.

Track source, manifests, lockfiles, instructions, skills, and intentional synthetic fixtures. Exclude live/private data, credentials, logs, dependencies, generated work, backups, archives, and release-copy repositories from ordinary staging and active discovery.

Verify instruction and skill discovery from the workspace root, transitional application directories, and final bot directories. A session started above a component must explicitly read its applicable rules; merely editing a nested path is not proof those instructions were loaded. Archived rules must not substitute for active guidance. [S3–S4]

### 4.3 Project memory and task continuity

`PROJECT_STATUS.md` records:

- Current objective, phase, scope, and one next concrete action.
- Relevant source/fingerprint, working-tree caveats, and installation/build identity.
- Separate **planned**, **implemented**, **applied to installation**, and **verified** status.
- Meaningful results, environments, and retained evidence links.
- Issues, assumptions, blockers, and remaining acceptance gates.
- Actual maintained commands, or an explicit statement that a tool is still planned.
- Links to migration/disposition ledgers and recovery instructions.

Update this checkpoint after meaningful milestones and before handoff. Use ordinary version history for older checkpoints; do not create competing `MEMORY.md`, `HANDOFF.md`, or timestamped status trees.

For substantial/resumed work, read the applicable instructions, brief checkpoint, relevant map section, and task-specific procedures; compare relevant fingerprints/current state before relying on old claims. Small edits do not require loading all history or running all tests.

At completion, record actual changes, evidence, incomplete work, and the next action. Decisions include reasons and supersession. Markdown is not another live registry, scheduler, or credential store. Keep secrets, private transcripts, and unnecessary user-data copies out of context records.

### 4.4 Reusable workflows and roles

Create instruction-only skills once their maintained tools exist:

| Skill | Trigger/input | Output |
|---|---|---|
| `.agents/skills/add-bot/SKILL.md` | New bot or existing project, identity/capabilities/state inputs | Uses maintained onboarding; validates, records versions/catalogs, leaves disabled/stopped |
| `.agents/skills/verify-change/SKILL.md` | Bounded changes and relevant fingerprints | Coordinator-selected, deduplicated checks and truthful evidence |
| `.agents/skills/close-task/SKILL.md` | Actual changes, applied state, checks, remaining work | Updated checkpoint and affected records, with incomplete gates visible |

Use the installed Codex version's supported skill format, concise YAML name/description, clear triggers, maintained references, and completion criteria. Verify discovery and small isolated examples. Skills call the same generator, coordinator, and policies; do not embed duplicate implementations.

`close-task` does not trigger unsolicited cleanup, automatic heavyweight verification, or promotion of reused results to fresh passes.

Document Coordinator, Implementer, and Verifier responsibilities. Use supported subagents when useful, with explicit file ownership and one coordinator for shared manifests, catalogs, root rules, integration decisions, and checkpoint edits. One agent may fill all roles. Custom agent configuration is unnecessary initially; validate any later configuration against the installed version and preserve unrelated settings.

Durable context depends on future sessions reading and maintaining these files; it is not automatic memory independent of them.

## 5. Preservation, migration, backups, and restore

### 5.1 State that must survive

Preserve complete keyed Home task/member/meta state, revisions/statuses, configuration, ownership, panel references, compatible saved identifiers, exports, and necessary recovery history.

Preserve French note bytes/names/order, approved source/snapshots, ownership, invitations, reader preferences/positions, drafts, history, prepared audio, cache/profile keys, saved audio references, and dedicated application-owned browser state. Exclude unrelated browser data.

Migrate relevant French AppData state to canonical data folders. Explicitly configure reader and editor to use the correct shared history location. Inventory both existing locations; preserve distinct necessary entries and resolve any real divergence through application-aware reconciliation, not a blind overwrite or latest-modified rule.

All supported production entry points use the canonical resolver. Missing expected state blocks startup; only explicit new-project creation initializes a new namespace. Normal shared maintenance leases do not replace the application's own concurrency protection.

### 5.2 Migration transaction and interruption recovery

Before moving application code/state:

1. Refresh writers, resolver inputs, repository boundaries, launchers, shortcuts/startup definitions, and space requirements.
2. Acquire the installation maintenance gate and quiesce relevant writers normally. Never force-close unsaved editor work.
3. Capture fresh semantic/file baselines and consistent SQLite snapshots, including committed WAL state through a supported snapshot mechanism. Do not treat copying the main database alone as a complete live backup. [S5]
4. Stage relocation, packages, recorded environments, compatibility launchers, and required fixture/tool extraction.
5. Verify keyed records, approved hashes, paths, snapshots, drafts/history, audio references, package assets, and entry points.
6. Rehearse isolated recovery using maintained tooling that does not depend on the unfinished supervisor.
7. Prepare and verify the complete recovery material—including private decryption verification of credential-containing material—before activation.
8. Switch the generation atomically and retain the gate through configuration/shortcut updates and activation checks.
9. Release only when active configuration and rollback instructions agree.

Persist a durable journal recording operation identity, source/target generation, phases, validation, publication, and completion. All supported production entry points check it. Incomplete activation blocks affected polling and writable startup even after the original process dies and its lock disappears. Read-only recovery inspection remains available.

Recovery completes the transaction or restores a coherent previous generation. Never infer completion from a marker alone or initialize empty state to get past a failure. Use equivalent recoverable publication for restore and multi-record registration.

Missing French credentials can block execution without blocking verified preservation. Unexplained changes block the affected activation.

Rollback restores recorded configuration/resolver inputs, shortcuts, startup definitions, and generation—not merely one marker. After new work has been accepted, preserve current state first and identify newer changes needing reconciliation.

### 5.3 Manual verified backups

Backups reserve affected resources, quiesce writers, and capture consistent application/core state and required assets. Open editor work blocks exclusive capture with an actionable explanation. Temporary quiescence belongs to the backup operation and does not rewrite desired state.

Installation-wide capture pauses schedule dispatch and state-mutating commands while capturing core/application state. In-memory status may continue. Requests waiting in a temporary queue are not yet durably Accepted. Commit queued commands normally afterward, applying Stop/Disable/Quit before reopening launch barriers.

Release capture resources and reconcile against current intent; perform verification on the captured copy separately. Never resume against intent remembered before capture.

Mark a set **Verified** only after archive/hash checks, database and semantic checks, asset-reference checks, and isolated restore preflight. Failed or partial capture/publication cannot replace the last valid set or become Verified.

Each set records relevant schemas, application/core/shared build identities, configuration/credential references, source fingerprints, assets, and verification evidence. Pin compatible artifacts or demonstrated reconstruction inputs while the recovery set depends on them. Document external prerequisites; a version label or requirements file alone is not proof of recoverability.

### 5.4 Restore

Run under exclusive recovery mode. Stop affected applications, close applicable core database connections, preserve current state, validate compatibility, stage the replacement, and activate through the journaled recovery process.

Reject incompatible restoration before replacing current state. Restore starts bots stopped and schedules disabled, establishes a no-replay boundary, discards stale leases/sessions/process ownership/pending runnable commands, and marks uncertain interrupted operations Indeterminate. Preserve application identities and compatible application state.

This stopped recovery mode overrides ordinary reconciliation of saved running intent. Require explicit resumption; never replay runnable commands from a restored backup.

Retained recovery sets and their reconstruction inputs take priority over routine cleanup limits.

## 6. Command semantics, supervision, and security

### 6.1 Authoritative intent and lifecycle

Core state alone owns enabled status, desired state, intent revision, attention, failure history, operation records, and observed runtime state. Manifests contain declarations, not conflicting runtime copies.

| Event | Required behavior |
|---|---|
| First Home/French enrollment | Establish running intent once after migration validation; unavailable credentials/identity block execution |
| Repeated installation/enrollment work | Preserve existing intent and deliberate stops |
| Future registration | Disabled and stopped |
| Enable | Change enabled status only; remain stopped |
| Start / Start all | Explicit running intent for eligible enabled bots; report blocked bots |
| Stop / Stop all | Persist stopped intent first; cancel/supersede pending launches and conflicting maintenance safely |
| Restart | Running intent; fully stop old owned tree before replacement; Start behavior if stopped |
| Newer Stop/Disable | Supersede retries, restart replacement, and earlier launch requests |
| Disable | Disabled/stopped intent |
| Quit core | Global launch barrier; stop managed trees while preserving intent |
| Ordinary reopening | Recover unfinished operations, then reconcile latest saved intent and external-instance holds |
| Scheduled Start | Explicit running intent after transactional schedule validation; show upcoming scheduled start while stopped |
| Explicit Retry | Revalidate, preserve desired state, create a new retry budget, retain diagnostics |
| Restore | Stopped recovery mode, schedules disabled, explicit resumption |

### 6.2 Durable acceptance and ordering

Commit the canonical target/action/payload, operation record, and applicable intent revision/change in one transaction before acknowledging durable acceptance. Keep Accepted and Completed distinct.

The same operation ID and request returns the original operation. Reuse with different target/action/payload is rejected without effects. Equivalent pending lifecycle requests may share work. A distinct conflicting Start/Restart or manual maintenance request returns Busy; a distinct request for an already active job is not silently treated as the original operation.

Stop, Disable, and Quit retain priority over the general Busy rule. They establish the applicable stop/launch barrier and cancel or supersede conflicting work under safe-cancellation rules.

Use short transactions and per-bot serialization; do not await network/process completion while holding command serialization. Before every actual launch, recheck current intent revision, enabled state, attention, external-instance hold, resource gates, and Quit/restore/activation barriers.

Recover unfinished operation records before normal startup reconciliation. Never replay old lifecycle records as fresh intent changes. Reconcile once against the newest persisted intent; mark old operations interrupted, superseded, or satisfied as appropriate. Unknown maintenance effects remain Indeterminate. Permitted safe retry retains its original identity and rechecks all gates.

Commit/write failures produce failed/blocked results and start no new work. Suspend new launches while core persistence is unhealthy. Best-effort stopping remains possible, but distinguish immediate process shutdown from a successfully saved persistent Stop. Do not claim a database write succeeded when it did not. [S6]

### 6.3 Locks, managed contract, and containment

Use:

- A current-user installation singleton lock for the core.
- A current-user, machine-wide polling lock keyed by verified numeric Telegram bot ID, independent of aliases, token changes, and workspace spelling.
- The polling lock before writable application state opens.
- Compatible maintenance leases for ordinary reader/editor/audio work.
- Exclusive reservations for migration/capture/restore and relevant jobs.
- Separate editor single-writer protection.
- Stable acquisition order for multiple resources.

Non-polling maintenance workers do not take a second polling lock.

Contract version 1 covers identity, canonical paths, capabilities/jobs, credentials, readiness, progress, cooperative stop, completion, and typed failures. Run executable argument lists directly without shell interpolation. Validate executable/entry-point targets; keep shell wrappers as controlled compatibility launchers rather than a route for arbitrary manifest command text.

Create managed processes suspended, assign Job Objects, then resume. Enable kill-on-close, prevent permitted breakaway paths, and keep the lifetime owner's handles private. Explicitly whitelist inherited handles; exclude Job Object handles, installation locks, unrelated pipes, and other runs' handles. [S7]

Each run gets dedicated bootstrap/control/event channels. Close unused ends and bootstrap after consumption. Keep protocol separate from stdout/stderr; maximum frame size is 64 KiB, with bounded 1 MiB diagnostic buffering per stream. Drain output continuously; overflow must not block control. Malformed frames produce sanitized failures without payload echoing.

### 6.4 Health and shutdown

Health evidence is tied to current run, sequence, process handle, and creation identity:

- **Running:** current process exists.
- **Ready:** initialization and state validation completed.
- **Connected:** a successful completed poll within the configured 90-second health window and responsive application progress.
- **Disconnected:** network failure with responsive application/control processing.
- **Unknown/Unresponsive:** stale telemetry or application progress.

Empty successful polls count; identity checks alone do not prove polling health. Use five-second telemetry, twenty-second telemetry staleness, ninety-second application-stall detection, and a ninety-second post-sleep recovery window. Advance progress only from actual application/control work and measure elapsed deadlines monotonically.

Home uses a five-second long poll; French uses ten seconds. Adapters enforce a thirty-second overall polling deadline and remain controllable during waits. Verify the actual transport's effective timeouts.

Shutdown stops intake, safely completes/cancels work, closes state, and acknowledges completion. Allow up to forty-five seconds, then terminate only owned trees. Quit sends stop requests concurrently. Drain cancelled maintenance/audio workers before releasing their resource reservations.

### 6.5 Failure classification and core health

Classify before retrying:

| Outcome | Policy |
|---|---|
| Missing/invalid credentials, wrong bot identity, polling conflict, unrecoverable configuration | Needs attention; no automatic process retries or unexpected-failure budget consumption |
| Responsive temporary network disconnection | Reconnect in the existing application; do not recreate processes merely for network loss |
| Unexpected exit, including unrequested zero exit, or watchdog-confirmed application failure | Count once per run; apply bounded retry |
| Manual/normal shutdown or successful finite job | No unexpected-failure increment |
| Termination caused by core loss | Interrupted by core loss; no independent per-bot crash increment |

Do not misclassify inability to reach Telegram as proof of invalid credentials.

Persist failure history in ten-minute windows. Retry after 2, 5, 15, and 30 seconds; the fifth eligible failure requires attention. Watchdog shutdown still counts exactly once as the originating failure. Manual stops stay stopped.

Abrupt core loss terminates managed trees through kill-on-close containment. Preserve intent, record interruption, and check integrity on reopening before reconciliation.

Explicitly own and observe essential core background tasks. Failure of supervision or command/control makes readiness fail, blocks new launches, exposes the error, and initiates bounded shutdown of owned processes. A scheduler-only failure stops dispatch and shows degraded scheduling; healthy ordinary bot controls may remain available. Keep normal per-bot failures isolated. An open HTTP listener is not core readiness. [S8]

### 6.6 Credentials, bot identity, and local authentication

Import Home's existing token locally without display. Request French's token once through masked local input only when no usable encrypted credential exists. Never request tokens in chat.

Use current-user Windows DPAPI and private file permissions. Protect credential-containing recovery originals, including nested repository snapshots, before ordinary archival/staging. No tokens in arguments, responses, validation errors, exports, logs, reports, or source history. Redact before persistent sinks. [S9]

Persist expected numeric Telegram identity separately from local project ID, display name, and token. Verify the current credential's identity before polling or application-side Telegram mutations. A mismatch leaves application ownership/state unchanged and requires attention.

Stage token replacement, verify the expected identity, then replace the working encrypted credential. Preserve the prior working credential on failed/mismatched replacement. Offline validation is separate from online identity verification; an unverified future bot remains disabled/stopped.

Before opening an existing dashboard or issuing a ticket, authenticate the live core over protected local IPC bound to installation and process/instance identity. A port, title, PID alone, or discovery file is insufficient. Discovery contains no reusable credential.

For named management pipes, set explicit private user/logon permissions and reject remote clients. Reject stale/mismatched instances. Port collisions produce an actionable failure or authenticated supported alternative; never kill an unrelated process or hand it credentials. [S10]

Exchange a single-use sixty-second launcher ticket through a URL fragment for an HttpOnly `SameSite=Strict` session. Remove the fragment promptly; invalidate sessions on core restart. Authenticate all private read APIs/event streams and controls. Controls require CSRF protection and exact Host/Origin checks. Render diagnostics as text. Preserve loopback-only bindings.

### 6.7 Telegram update durability

Before automatic recovery is enabled, inspect each real adapter/framework's acknowledgment, state-commit, and replay behavior.

Do not advance acknowledgment past state-changing updates whose required local effects are not durable. Prevent replay from applying the same non-idempotent local mutation twice; where supported, commit the processed update identity with the mutation. Use the smallest explicit, compatible persistence fix required by an observed defect.

Preserve pending updates during ordinary startup/recovery. Do not silently discard queued updates or remove an unexpected webhook as housekeeping. Only the owning polling loop calls `getUpdates`; health observes its reports.

Document ambiguous remote effects. A lost response does not establish whether a Telegram send succeeded; do not promise exactly-once remote effects or blindly repeat uncertain non-idempotent effects. Telegram confirmation offsets and identity semantics inform these checks. [S11]

## 7. Schedules and registered maintenance

Schedules begin disabled. Support one-time UTC instants, fixed-anchor intervals, and timezone-aware daily/weekly schedules using `America/New_York` and explicit Windows timezone data. Support lifecycle actions and declared maintenance jobs. [S12]

Required time behavior:

- Skip nonexistent spring times; execute the first repeated autumn occurrence once.
- Record and skip occurrences during core downtime or PC suspension before considering normal lateness.
- Allow thirty seconds of lateness only during continuously active, awake operation.
- Persist a nondecreasing boundary independently of schedule revision.
- Preserve consumed claims/skips across revisions; enforce uniqueness by schedule ID/due UTC.
- Edited future occurrences fall strictly after both the boundary and current time.
- Enabling establishes a future activation boundary; never backfill disabled time. Past one-time schedules become missed/expired.
- Claim before dispatch. Interrupted unknown execution becomes Indeterminate; automatically repeat only explicitly safe jobs.

Command-service acceptance is the dispatch boundary. Atomically recheck schedule enabled status/revision when accepting the occurrence and applying intent. Disable/edit invalidates claims not yet accepted. Keep them consumed with cancelled/skipped reasons.

Already accepted work stays visible as pending/active and follows existing Stop/cancellation rules. Disabling the schedule does not hide that work or undo a later manual intent. Actual launches still recheck latest intent and resource gates.

Allow at most one active execution of the same registered job for a bot. Different jobs respect declared reservations. A busy scheduled occurrence becomes durably **Skipped (busy)** with no catch-up queue. A distinct conflicting manual request returns Busy. Stop/Disable/Quit retain priority.

Maintain one operation catalog: purpose, owner, entry point, environment, inputs, write scope, resources, timeout/cancellation, repetition safety, output, retention, verification. Distinguish production jobs, developer tools, one-time migrations, and historical evidence. Expose only selected production operations in the dashboard.

Retain existing source-integrity checks, audio preparation/validation, backup verification, simulator checks, local release preparation, and cleanup. Audio uses accepted content/voice profiles, validated atomic publication, committed completion checkpoints, safe cancellation/draining, and revalidation on resume. Cleanup introduces no new service or autostart behavior.

## 8. Future-bot creation and registration

Use one maintained generator/registration service and validator set with dashboard and CLI adapters. The `add-bot` skill calls these interfaces. `templates/python_bot/` is the sole template source.

Planned responsibilities:

- Core registration service: orchestration and recoverable activation.
- `tools/bots/manage_bot.py`: CLI adapter and offline inspection/staging.
- `docs/ADDING_A_BOT.md`: actual supported entry points once implemented.

A new project receives an installable package, metadata/dependencies, entry point, meaningful ID/display name, versioned nonsecret manifest, independent environment/state namespace, shared-runtime identity, managed/standalone hooks, credential/health/stop/failure integration, README, scoped instructions, isolated fixtures, applicable contract tests, and private/generated exclusions. Record template/runtime versions.

The manifest declares identity, executable argument list, working directory, canonical data/log paths, credential reference, capabilities, jobs, and contract/schema version. Do not put tokens or competing enabled/desired state in it.

Include only requested capabilities. Validate Windows-invalid/reserved names, trailing dots/spaces, traversal, resolved links, case-insensitive/normalized collisions, package names, Telegram identities, and existing project/state directories. Never overwrite existing projects or state during creation.

For an existing project, inspect instructions, dependencies, entry points, state, and tests before adapting it. Preserve its source, lockfiles, rules, and behavior. Never replace them with template defaults or initialize missing expected state.

Validate offline first using isolated state and fake credentials/transports. Online identity/credential checks are a separate stage; offline success does not imply connection verification.

Stage manifest, registry, project map, suite catalog, and operation-catalog updates as a recoverable registration operation. Publish no partially active project. Registration stays disabled/stopped; explicit Enable leaves stopped, and explicit Start is required.

Compliant additions require no Home/French business changes or supervisor special cases. Contract changes require a versioned decision and relevant consumer verification.

## 9. Controlled cleanup and retention

### 9.1 Classification and removal gates

Classify every candidate:

1. Active source/state.
2. Maintained tool/test.
3. Required recovery/retained evidence.
4. Reproducible generated output or superseded dependency.
5. Proven obsolete code/wrapper/marker/copy.
6. Unresolved.

For each proposed removal, record exact path, owner, class, reason, consumers checked, preservation/reproduction route, verification, disposition, and measured bytes.

Check imports, dynamic loading, subprocesses, manifests/configuration, launchers/shortcuts/startup, UI assets, discovery, tests/releases, and documented manual commands.

Removal requires extraction of unique source/scenarios/state/evidence; verified replacement, sealed recovery, or actual rebuild; relevant final-path/regression checks; no active consumer/run ownership; exact absolute target recheck; and no link traversal outside scope.

Apply small evidenced batches. Eligible in-scope removals need no repetitive permission requests. Dates or absence of imports alone do not establish obsolescence.

Never generically delete WAL/SHM, the only source or rollback route, unique Git work, approved notes, required drafts/history/audio, or active dependencies. Old environments are removable only after final replacements/reconstruction and recovery requirements pass.

Keep unresolved items owned, explained, and pinned with resolution conditions. Quarantine only when moving cannot break consumers. Preserve retained historical bytes; remove eligible redundant archives as complete inventoried units.

Measure actual reclaimed allocation where supported, logical bytes separately, and net footprint including new environments/backups. Moving material to an archive is not reclaimed space.

### 9.2 Bounded outputs with protected evidence

Each run records owner, command, inputs/build identity, outcome, output location, and retention class. Use `work/tests/<run-id>/<component>/...` and corresponding build/migration areas. Keep routine summaries compact; capture bulky diagnostics when useful; promote unique failures into maintained fixtures.

Initial configurable policy:

- Latest three completed routine bundles per suite/environment.
- Latest successful full acceptance record.
- Unresolved failures and explicit migration/recovery/acceptance pins.
- A 512 MiB soft routine-output budget and thirty-day review threshold, adjusted using measured growth.

Pins override routine caps. Retain pre-migration recovery through acceptance and until a later verified recovery point exists; prune only without losing uniquely required state or compatible recovery inputs.

Protect evidence supporting current checkpoint claims, reused results, unresolved issues, and retained recovery. Before pruning referenced output, preserve required summary/provenance/unique reproduction material and update links; otherwise withdraw the unsupported claim. Failed outcomes remain visible while redundant bulk from resolved failures may expire.

Exclude dependency, generated, archive, backup, and release-copy trees from active source/test discovery and ordinary indexing. Deliberate recovery/audit tools may inspect them within their declared scope.

## 10. Testing and trustworthy evidence

### 10.1 One maintained coordinator

Implement `tools/testing/run_tests.py` using the standard library, a stable suite catalog, explicit Python/Node environments, affected-input selection, deduplicated suite IDs, declared resources, direct argument-list invocation, isolated outputs, and one redacted summary.

Keep existing frameworks and meaningful scenarios. Catalog each suite's ID, owner, command, environment, affected inputs, platform/resources, outputs, and category. Share implementation checks for matching relevant build/environment fingerprints, then verify each consumer. Run common adapter contracts against every affected bot.

Preserve Home task/storage/batch/category/input/panel/simulator cases; French notes/editor/store/history/tense/ownership/startup/transport/audio cases; the audio fixture; and CJS editor/columns/rows/expressions/notebooks/speech/undo coverage. Move icon generation to asset tooling.

Inspect Home's simulator before sharing infrastructure; retain French-specific transport/audio fixtures where behavior differs.

### 10.2 Selection and isolation

Use tiers: smoke, affected checks, shared compatibility, integration/Windows/UI, justified performance/stress, final acceptance, and separate live/phone verification.

- Documentation changes normally use documentation/path checks.
- Relocation checks installed imports, entry points, assets, and canonical paths.
- Shared/dependency changes expand to actual consumers.
- UI/platform changes select their respective suites.
- Uncertain impact broadens affected-component coverage.
- Failures lead to a reproducer and relevant reruns; keep failed outcomes visible.
- Full integrated acceptance runs on the completed revision, deduplicating overlap.

Bound offline parallelism and isolate roots, databases, caches, identities, ports, and browser contexts. Serialize exclusive desktop/audio/performance/live resources. Contention tests own their entire scenario. Cleanup terminates fixture-owned processes only.

Use fake clocks/transports normally. Keep native Windows checks for DPAPI, Job Objects, pipe inheritance/permissions, locks, launchers, and actual platform behavior. Offline tests do not use production credentials/state or regenerate production audio; preservation compares against the current protected capture.

All crash, disk-full, corruption, and fault-injection scenarios use isolated fixtures. Do not fill, interrupt, or corrupt the production filesystem to test failures.

Verify installed builds from unrelated working directories without depending on obsolete source trees. Commands work through an absolute coordinator path.

### 10.3 Commands and results

Planned commands, available only after implemented and validated:

```powershell
py -3.11 tools\environments\prepare.py --all --locked
py -3.11 tools\testing\run_tests.py --list
py -3.11 tools\testing\run_tests.py --profile smoke
py -3.11 tools\testing\run_tests.py --suite home.unit --suite french.unit
py -3.11 tools\testing\run_tests.py --profile affected --changed-path bots\home
py -3.11 tools\testing\run_tests.py --profile shared-compatibility
py -3.11 tools\testing\run_tests.py --profile docs-context
py -3.11 tools\testing\run_tests.py --profile integration --profile windows --profile ui
py -3.11 tools\testing\run_tests.py --profile performance
py -3.11 tools\testing\run_tests.py --profile acceptance --fresh
py -3.11 tools\testing\run_tests.py --profile live
```

Before these exist, use and record actual legacy commands. Never present planned tools as installed.

Record source/working-tree, installed package, dependency, interpreter, fixture/configuration, harness, and platform fingerprints. Reuse evidence only when relevant inputs match; label its original run **Reused evidence**.

Results distinguish **Passed, Failed, Blocked, Skipped, Not applicable, and Reused evidence**. Historical reports are not fresh passes. Report both original failures and later outcomes; do not hide instability through retries.

Measure duration/artifact growth and demonstrate a second routine cycle with bounded output. Live Telegram tests verify both bots together after local gates and credentials are available. Use scoped reversible interactions and report phone verification separately.

## 11. Seven implementation phases

| Phase | Work | Exit evidence |
|---|---|---|
| **1 — Inventory and guidance** | Refresh actual files/rules/Git/tool state; protect nested repository and credentials; prepare exclusions/local history; create instruction scaffold, canonical plan/map/decisions/checkpoint | Inventory and ownership/disposition draft; protected repository restoration evidence; instruction discovery; no secrets in initial staging |
| **2 — Protection and baseline** | Capture fresh state, semantic hashes, verified recovery; exact relocation maps; minimal suite catalog/coordinator; journal/recovery tooling sufficient for staged work | Current preservation baseline; independently rehearsed recovery; compatibility inputs; fault-safe publication design |
| **3 — Minimal relocation** | Package apps/shared runtime; rebuild final environments; repair paths/launchers/fixtures; migrate AppData/history; verify standalone/editor and actual-adapter update ordering | Installed final-path checks, preserved state/assets, proper standalone ownership, editor drafts/history, update commit/replay evidence |
| **4 — Core management** | Commands/intent/IPC, authenticated launcher/UI, credentials/identity, supervision/containment, health/failure handling, capture/restore | Lifecycle and command crash/storage checks, authenticated handoff, native containment, essential-task health, concurrent bots when credentials permit |
| **5 — Consolidation** | Justified names/shared extraction; maintained tools/scenarios; completed test coordination and documentation | Clear ownership/map; preserved distinct assertions; correct environments; no active obsolete-source dependency; measured verification efficiency |
| **6 — Controlled cleanup** | Small ledger-backed removals after replacement/recovery/compatibility gates; preserve pins/unresolved material | Actual removals and net footprint; surviving unique Git/source/state; recovery/evidence dependencies remain usable |
| **7 — Extensibility and acceptance** | Complete scheduling/onboarding/template, actual onboarding docs, three skills/context workflows, retention, final integrated acceptance | Acceptance matrix below, fresh-session resumption/discovery, generated-bot checks, bounded outputs, separate offline/Windows/live/phone status |

Implement and test each guarantee in its owning phase; final acceptance is not the first time a safety-critical behavior is exercised. Maintain checkpoint/decisions/runbooks throughout. Blocked live checks do not prevent independent safe work, but remain visible and keep necessary recovery protected.

## 12. Acceptance map

Populate actual suite IDs, commands, source fingerprints, and evidence links for these stable requirement IDs. A row is not Passed merely because its test is planned. Broad rows may link to several existing focused suites.

| ID | Required demonstration | Phase |
|---|---|---|
| AC-01 | Fresh semantic/file baseline; keyed Home tasks/members/meta/panels and French notes/ownership/preferences/drafts/history/audio preserved | 2–3 |
| AC-02 | Complete nested Git/index/worktree/ignored/dangling-object preservation and restoration; no unauthorized remote action | 1–2 |
| AC-03 | Interrupt migration before/after generation switch and before config/shortcut publication; fresh launch recovers coherently or stays blocked without empty state | 2–3 |
| AC-04 | Installed packages/assets/entry points work from unrelated CWD; expected missing state blocks; no obsolete/archived-source import dependency | 3 |
| AC-05 | Managed/standalone lifetime ownership; finite CLI exit safety; both launch orders; external exit/core reopen preserve explicit takeover hold | 3–4 |
| AC-06 | Repeated/concurrent launcher clicks create one core/poller; browser close is harmless; saved stops survive ordinary reopening | 4 |
| AC-07 | Stop supersedes pending retry/restart/backup resume; global controls retain priority; manual stops remain stopped | 4 |
| AC-08 | Crash after Restart acceptance, after newer Stop, and after replacement launch/before completion; no stale intent replay or duplicate replacement | 4 |
| AC-09 | Operation-ID reuse with changed payload rejected; ambiguous maintenance effect stays Indeterminate; accepted/completed states truthful | 4 |
| AC-10 | Inject lock contention/write denial/disk-full at commits/publication; no false saved acknowledgment or new work; last valid state/backup survives | 2–4 |
| AC-11 | Job containment on crash/timeout; current-run health; responsive control under output pressure; inherited-handle isolation | 4 |
| AC-12 | Terminal credential/config/identity/conflict outcomes cause attention without retries; unexpected failures obey 2/5/15/30 delays and fifth-failure stop; watchdog counts once | 4 |
| AC-13 | Core-caused exits are interruptions; supervisor failure with live HTTP makes readiness unhealthy and stops owned trees; scheduler-only failure is visible and stops dispatch | 4, 7 |
| AC-14 | Wrong-bot valid credential, duplicate aliases, and mismatched rotation cause no wrong polling/sends/ownership changes; prior working credential survives | 3–4 |
| AC-15 | Real-adapter fixtures crash before commit, after commit/before acknowledgment, and after acknowledgment; queued/replayed updates preserve local effects without duplication; unexpected webhook is actionable | 3–4 |
| AC-16 | Unrelated listener, stale discovery, wrong IPC identity, unauthorized IPC access, and ticket replay yield no false handoff, leaked credentials, or unrelated-process termination | 4 |
| AC-17 | Actual isolated French editor with seeded unsaved draft/history/reader position survives relocation/profile/origin changes, reader restart, Quit, core crash/task failure | 3–4 |
| AC-18 | Manual consistent core/app capture, isolated verification while production may resume, corrupted/incompatible set rejection, and stopped/no-replay restore | 2–4 |
| AC-19 | Backup at version A remains recoverable after fixture install B or rejects before modifying B; cleanup preserves required compatible inputs | 5–7 |
| AC-20 | One-time/interval/daily/weekly schedules persist; DST, wall/timezone changes, sleep/downtime, edits, restore, and disabled time do not replay consumed occurrences | 7 |
| AC-21 | Hold job through next due time: one worker, durable Skipped (busy), and no delayed execution; distinct manual conflict is Busy; Stop priority retained | 7 |
| AC-22 | Pause after claim before acceptance; disable/edit invalidates it. Test both acceptance/disable orderings; accepted work visible/cancellable. Past one-time enable does not catch up | 7 |
| AC-23 | Existing-project preservation; malformed/colliding names/paths/identities, unsupported contracts, and missing-state rejection; recoverable registration publication | 7 |
| AC-24 | Generated bot installs in its own environment and passes applicable common contracts with fake credentials; registration/Enable/validation never starts it implicitly | 7 |
| AC-25 | Safe audio cancellation/draining, validated atomic publication, checkpoint and asset revalidation on resume | 3–7 |
| AC-26 | Synthetic token canary absent from errors/logs/reports/staged source/unencrypted archive members; protected recovery credentials decrypt only through intended procedure | 1–7 |
| AC-27 | Small evidenced cleanup batches preserve unique source/Git/scenarios/data; correct dispositions and measured net footprint | 6 |
| AC-28 | Four-run retention fixture includes resolved failure and reused result; outcomes/current links/reproducers remain valid; redundant bulk expires; second routine cycle bounded | 6–7 |
| AC-29 | Root/transitional/final-bot instruction chains and all three skills discovered in fresh sessions; actual entry points/docs links resolve | 1–7 |
| AC-30 | Fresh session finds objective, verified work, blockers, evidence, and next action without old chat; isolated add-bot/verify-change/close-task examples behave as specified | 7 |
| AC-31 | Existing regressions in correct environments; shared checks deduplicated without omitting actual consumers; native Windows/UI acceptance retained | 2–7 |
| AC-32 | Both real Telegram bots respond together after local gates and credentials; live results and separate phone checks are accurately reported | 4, 7 |

## 13. Completion, operating limits, and sources

### 13.1 Required handoff outputs

Deliver the working root launchers/dashboard, independent applications/environments, canonical configuration, protected credentials, migrated state, maintained tools and catalogs, template/onboarding, scoped instructions/skills, and runbooks.

Provide the inventory/disposition ledger, current baseline/preservation comparison, recovery manifests and restore instructions, cleanup ledger/measurements, acceptance map with real evidence, and concise final `PROJECT_STATUS.md`.

Report separately: plan completion, implementation, applied migration, cleanup/deletion, offline checks, native Windows checks, live Telegram, phone verification, reused evidence, blockers, and remaining limitations. Do not call the whole installation verified while required checks are blocked.

### 13.2 Operating limits to state plainly

Closing the browser leaves the core running; PC sleep, shutdown, or an absent core interrupts service. Telegram does not retain incoming updates indefinitely; its documented limit is 24 hours. Explain backlog and downtime behavior without changing the agreed startup/power policy. [S11]

Backups on the same drive provide rollback but cannot survive loss of that drive. Document the scope and allow a manually selected additional destination if available. Current-user DPAPI may require token re-entry after moving to another PC or losing the profile. [S9]

Keep these limitations distinct from implementation failures. Do not introduce unrequested hosting, cloud backup services, or always-running AI agents to address them.

### 13.3 Primary documentation

The requirements above are project design decisions; the references support platform behavior and implementation details.

- **S1:** [Python packaging: src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- **S2:** [pip: secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)
- **S3:** [OpenAI: AGENTS.md instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- **S4:** [OpenAI: skills](https://learn.chatgpt.com/docs/build-skills)
- **S5:** [SQLite backup API](https://www.sqlite.org/backup.html)
- **S6:** [SQLite transactions and error handling](https://www.sqlite.org/lang_transaction.html)
- **S7:** [Microsoft: Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- **S8:** [Python 3.11: asynchronous task ownership](https://docs.python.org/3.11/library/asyncio-task.html)
- **S9:** [Microsoft: CryptProtectData / DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
- **S10:** [Microsoft: named-pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights)
- **S11:** [Telegram Bot API](https://core.telegram.org/bots/api)
- **S12:** [Python 3.11: zoneinfo](https://docs.python.org/3.11/library/zoneinfo.html)
- [FastAPI: manual deployment](https://fastapi.tiangolo.com/deployment/manually/)
- [HTML: Web Storage and origin](https://html.spec.whatwg.org/multipage/webstorage.html#dom-localstorage)

Execute the established phases, record evidence, and keep the canonical plan current. Resolve routine implementation details through inspection and tests rather than repeatedly expanding the architecture.

