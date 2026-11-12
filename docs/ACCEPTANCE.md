# Acceptance evidence

Installed build identity: `21db105f122078f02f847712775c5d6f28a3a2a75016432e79d9a6339937e566` (SHA256 of work/build/build-manifest.json), five consumer environments and 38 retained wheels. Implementation source is local commit `fd54ae2d3b8a5f5f4c1b9fb6328fc78ad55cfb20`; later evidence/documentation bookkeeping does not change installed application code.

[Final local acceptance](../work/tests/20261111T221408Z-146cc5ca/summary.json) passed **27/27 suites**, with no Failed, Blocked, Skipped or Reused outcomes. This includes 500 component Python tests, 29 native tests, eight French browser suites, core/Home browser checks, actual independent editor lifetime, seeded Home simulations, and a generated bot in its own installed environment with six nested fake contracts. Faults/clocks/transports use isolated fixtures and do not establish phone behavior.

[Initial real polling](../work/tests/20261111T223030Z-f0b9d5a8/summary.json), [polling after backup and cleanup](../work/tests/20261111T223607Z-73ddddb9/summary.json), and [independent authenticated status](../work/live/independent-status.json) show both real bots ready and Connected. Observers sent no messages. Inbound replies and phone acceptance remain unverified; on 2026-11-12 the user undertook those checks, with no result reported yet. AC-32 remains open.

The 2026-11-12 closure changes documentation/disposition records only. Its [read-only fingerprint review](../work/context/closure-20261112-build-review.json) matches all 92 application source hashes and the relevant suite, harness, input, platform and installed-environment fingerprints for 26 non-documentation acceptance suites. These are **Reused evidence (original Passed)**, not fresh runtime executions. The [closure receipt](../work/context/closure-20261112.json) links fresh documentation validation, updated-checkpoint resumption and the latest read-only runtime observation. No heavy runtime suite or Telegram interaction is needed for this documentation change.

The [first full attempt](../work/tests/20261111T215907Z-789c76e9/summary.json) retains 17 Passed/10 Failed. Corrections preserve the original assertions: Windows temporary-CWD cleanup, restored selected tabs/unsaved drafts, deterministic broken-pipe injection, cancellation completion in the editor fixture, Home stale-callback campaign handling and narrow-screen labels. Home also rejects incomplete existing state before writable startup. Actual activation failure/recovery is preserved in [the recovery runbook](RECOVERY.md).

| ID | Demonstrated scope | Outcome |
|---|---|---|
| AC-01 | Pre-poll preservation: 2375 baseline files, 39 SQLite snapshots, 14 notes; ownership/preferences/drafts/history/audio | Passed |
| AC-02 | Original recovery rehearsal: full nested Git/index/worktree/ignored/dangling objects and directories | Passed |
| AC-03 | recovery.unit interrupted publication; actual blocked activation, guarded rollback and complete reactivation | Passed |
| AC-04 | Four installed probes from unrelated CWD; app path/missing-state/asset regressions | Passed |
| AC-05 | Native locks/lifetime, core persisted external holds, real outside-owner exit/core reopen | Passed |
| AC-06 | Actual concurrent launcher/reopen, saved Stop and single authenticated core; core.ui | Passed |
| AC-07 | core.unit Stop priority over retry/restart/capture and cancellation drain | Passed |
| AC-08 | core.unit Restart/crash/newer-Stop/replacement reconciliation | Passed |
| AC-09 | core.unit operation ID/payload, durable acceptance and Indeterminate maintenance | Passed |
| AC-10 | Isolated commit denial/disk-full/publication faults; actual rename failure stayed blocked | Passed |
| AC-11 | Native Jobs/descendants/handle isolation/protocol/output/control/bootstrap faults and current-run health | Passed |
| AC-12 | Terminal failure classification, 2/5/15/30 retry budget, fifth failure/watchdog once | Passed |
| AC-13 | Essential-task readiness/shutdown, scheduler degradation, actual editor/core-loss fixture | Passed |
| AC-14 | DPAPI replacement/identity fixtures, duplicate identities, actual expected identities verified before polling | Passed |
| AC-15 | Real-adapter commit/replay fixtures, offsets/queue preservation, webhook and uncertain-send guards | Passed |
| AC-16 | Protected IPC, wrong/stale discovery, restricted token, ticket replay, unrelated listener | Passed |
| AC-17 | Actual French editor/browser relocation, drafts/history/position and independent lifetime | Passed |
| AC-18 | Consistent capture, corruption rejection, no-replay restore; deployed Verified set and app validators | Passed |
| AC-19 | Incompatible build rejection before mutation; compatible wheels/recovery inputs pinned | Passed |
| AC-20 | scheduler.unit UTC/interval/daily/weekly, DST, backward time, sleep/downtime/edit/restore/enable boundaries | Passed |
| AC-21 | One job, consumed Skipped(busy), manual Busy and Stop priority | Passed |
| AC-22 | Transactional claim/disable/edit orderings, accepted cancellation and expired one-time activation | Passed |
| AC-23 | Existing-project preservation, name/path/identity/contract validation and recoverable registration | Passed |
| AC-24 | Generated own wheel/environment, disabled/stopped registration, Enable stays stopped, six fake contracts | Passed |
| AC-25 | Audio atomic publication/checkpoints/revalidation/cancellation/drained workers; fake audio offline | Passed |
| AC-26 | DPAPI/ACL/tamper, staged-source/wheel/canonical-archive credential scans | Passed |
| AC-27 | Four exact cleanup units, 143953898 logical bytes removed, unique Git/source/state and owned pins retained | Passed |
| AC-28 | coordinator.unit four-run retention/failure/reuse fixture; two pinned, matching-fingerprint routine cycles at 8893 bytes each; applied retention preserves outcomes/provenance | Passed |
| AC-29 | docs.context paths/skills/links; recorded instruction/skill discovery at all five root/transitional/final-bot locations; updated-checkpoint check in closure receipt | Passed |
| AC-30 | Sole checkpoint, isolated generated-bot example, coordinator-selected verification and close-task closure; fresh-session resume of updated checkpoint | Passed |
| AC-31 | All 27 final suites in declared environments, shared deduplication and all consumers/native/browser scope | Passed |
| AC-32 | Both real pollers simultaneously Connected before/after backup; no user phone interaction | Polling Passed; inbound replies/phone unverified |

Preservation: [pre-poll comparison](../work/migration/initial/applied-preservation.json),[original restoration](../work/recovery/original-rehearsal-verified/summary.json),[Verified deployed recovery](../backups/recovery-20261111T223133Z-e30a36f2/verification.json). Security: [staged source/wheels](../work/security/source-and-release.json),[canonical archive members](../work/security/canonical-archive-check.json). Cleanup: [ledger](cleanup-ledger.json),[gates/pins](../work/cleanup/final-replacement-gates.json).

Context/discovery evidence from 2026-11-11:

| Session location | Instruction/skill discovery |
|---|---|
| Workspace root | [Passed](../work/context/probe-1794436971814223700/result.json) |
| Original Home directory | [Passed](../work/context/probe-1794436972213165500/result.json) |
| Original French directory | [Passed](../work/context/probe-1794437058392535700/result.json) |
| Final Home directory | [Passed](../work/context/probe-1794437057491282400/result.json) |
| Final French directory | [Passed](../work/context/probe-1794437133565006800/result.json) |

These five recorded passes read the pre-closure checkpoint; their saved input hashes preserve that scope. A fresh root session checks the final checkpoint through the closure receipt. The [initial context failure](../work/context/probe-1794426555344075700/result.json) remains Failed: Windows error 1385 prevented reading files. It is not promoted by the later successful sessions.

Retention evidence: [two actual routine cycles](../work/retention/routine-cycle-evidence.json), [first pinned summary](../work/tests/20261111T224201Z-a6bbed55/summary.json), [second pinned summary](../work/tests/20261111T224307Z-71823240/summary.json), and [applied retention](../work/cleanup/retention-final-applied.json). Sixteen redundant routine bundles expired, reclaiming 120386 logical bytes; compact outcomes, current links and required failure evidence remain. This routine-output removal is separate from the four application/dependency cleanup entries.

Restricted-token IPC testing does not constitute signing in to a separately created second Windows profile. Same-drive recovery and Windows-profile limits remain in operations/recovery. No Windows autostart, power policy, unrelated process, remote repository or hosting change was made.
