# Schedules

Schedules start disabled. Create a one-time UTC instant (`kind: once`, `at`), a fixed-anchor interval (`kind: interval`, `anchor`, `seconds`), or a daily/weekly local time (`kind: daily|weekly`, `time: HH:MM`, `timezone: America/New_York`, and weekly `weekday: 0..6`, Monday first). The core environment installs explicit `tzdata` for Windows.

Enable establishes a boundary strictly after the current instant; disabled time is never replayed. Editing advances the independent boundary and revision and cancels claims that have not been accepted. A past one-time occurrence is retained as Skipped/expired. Skips, claims and accepted occurrences remain unique by schedule ID and UTC due instant across edits.

Spring clock gaps have no execution. Autumn repeated times use the first occurrence once. The first scheduler tick after core reopening and ticks after a suspension/clock discontinuity skip due work before normal lateness is considered. Only continuous awake operation allows up to 30 seconds of lateness. A backward clock movement cannot lower the stored boundary.

The scheduler claims an occurrence, then submits it through the command service. The command service calls `Scheduler.validate_claim(db, claim)` inside the same transaction that accepts the operation and updates intent. This is the ordering boundary: Disable/Edit before acceptance invalidates the claim; acceptance before Disable leaves visible accepted work. Stop/Disable/Quit cancellation semantics remain authoritative.

Busy scheduled work becomes durably Skipped (busy), with no delayed queue. The command service enforces registered job/resource conflicts; the scheduler never creates processes. Unknown interrupted claims are Indeterminate and are never reissued as fresh work. Restore disables schedules and advances boundaries while preserving consumed occurrence history.

Maintained API: `Scheduler(service).create(...)`, `.edit(...)`, `.list()`, `.occurrences(...)`, `.tick(...)`, and async `.run()`. `claim_due` and `dispatch_claim` expose the two dispatch stages for controlled tests. The owning core observes scheduler task failure and reports degraded scheduling while leaving healthy ordinary controls available.
