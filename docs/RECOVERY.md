# Recovery and manual backups

The implementation is the installed `bot_center.recovery` module. Its source is [recovery.py](../core/src/bot_center/recovery.py); [the CLI adapter](../tools/recovery/recovery.py) imports that installed module. The module can also run directly with Python 3.11 for read-only journal inspection and recovery before a dashboard is available.

## Capture and verification

Use the dashboard's Backup operation or the supported management CLI. The persistent core pauses dispatch and writable command acceptance, closes or quiesces affected readers, and refuses exclusive capture while the French editor owns unsaved work. Temporary capture never changes intended running/stopped state. A waiting request has not been durably Accepted. Stop/Disable/Quit receive priority when queued commands are committed after capture.

Capture uses `sqlite3.Connection.backup` for every SQLite database, including committed WAL records. It captures notes, accepted snapshots, ownership/preferences/positions, drafts/history, audio assets, configuration, encrypted credentials and compatible artifact inputs. It omits transient locks/stop requests/discovery and raw WAL/SHM copies. DPAPI encryption occurs before ordinary recovery publication.

The core releases capture reservations and reconciles against the newest intent before verification. Verification privately reconstructs the captured copy, checks every member hash/length, opens every SQLite database and compares keyed semantic hashes. A set becomes **Verified** only after this independent preflight. Failed/partial captures retain their failure record and cannot replace `backups/latest-verified.json`.

The offline CLI is for a closed installation. `--quiesced` asserts that all affected application writers and core database connections have been closed; it is not a command to force-close unsaved work.

```powershell
.\.venvs\core\Scripts\python.exe tools\recovery\recovery.py --root 'D:\telegram bots' list
.\.venvs\core\Scripts\python.exe tools\recovery\recovery.py --root 'D:\telegram bots' capture --quiesced
.\.venvs\core\Scripts\python.exe tools\recovery\recovery.py --root 'D:\telegram bots' verify 'D:\telegram bots\backups\<set>'
```

An additional destination can be selected with `capture --quiesced --destination '<directory>'`. The new directory must not already exist. A backup on D: supports rollback but cannot survive failure of D:. Copying its encrypted set to another manually chosen drive provides a separate storage copy; this installation creates no cloud backup service.

## Restore

Close the core/readers and the French editor normally. Preserve unsaved editor work. Use the set's pinned compatible builds; a build/schema mismatch rejects restoration before modifying current state. The restore command preserves the current installation in a fresh Verified set, reconstructs the replacement in private staging and activates through the journal.

```powershell
.\.venvs\core\Scripts\python.exe tools\recovery\recovery.py --root 'D:\telegram bots' restore 'D:\telegram bots\backups\<set>' --quiesced
```

Restored bots have stopped intent, schedules are disabled and their nondecreasing no-replay boundaries advance. Pending runnable operations/claims become Indeterminate; consumed occurrence identities remain consumed. Process ownership, sessions and retry timers are discarded. Reopen the dashboard, inspect the recovery result and explicitly Start the desired bots. Ordinary reopening never resumes restored running intent.

Recovery sets retain the build/source/configuration fingerprints and pinned reconstruction inputs they need. Cleanup cannot remove those inputs merely because a newer environment exists. Current-user DPAPI needs the original Windows user profile; another PC or a lost profile can require masked local token re-entry.

## Interrupted activation

`config/activation-journal.json` records source/target generations, operation identity, validation, publication phases and completion. Every supported writable/polling entry point blocks on an unfinished journal even after its original lock owner dies. Read-only inspection remains available:

```powershell
py -3.11 core\src\bot_center\recovery.py --root 'D:\telegram bots' journal
```

Before generation publication, the journal seals the previous resolver/configuration/launcher files. An interrupted migration either finishes the explicitly validated publications or restores those files through `rollback-publication`. Never delete the journal or create empty state to bypass it. After newer work has been accepted, preserve that work and identify reconciliation needs before rollback.

`rollback-publication` restores declared file publications. A restore interrupted during directory replacement also records `replacement_directory` and `rollback_directory`. Keep the startup barrier, preserve both directories and use the recorded previous trees with their matching configuration; never infer a complete restore from a single marker.

## Verified deployed installation

The first deployed recovery point is [recovery-20261111T223133Z-e30a36f2](../backups/recovery-20261111T223133Z-e30a36f2/manifest.json): 2,424 privately reconstructed files, consistent SQLite snapshots, pinned build inputs and both application integrity validators Passed. The core paused capture work without changing running intent, then both bots resumed Connected. [Durable operation evidence](../work/recovery/installation-ready-backup-operation.json) records completion. Keep this set and the original protected capture pinned.

Initial activation encountered a Windows directory-rename denial before generation publication. The journal kept writable startup and polling blocked. The fully copied French tree was independently compared, adopted under exclusive maintenance locks using the existing bounded replacement helper, and declared publications were rolled back before a new complete activation. [Original failure](../work/migration/initial/activation-failed-first.json), [guarded recovery](../work/migration/initial/activation-recovery.json), [pre-poll preservation](../work/migration/initial/applied-preservation.json), and [completed activation](../work/migration/initial/activation-result.json) remain available. The driver now uses that same tested bounded helper for directory/shortcut publication. No empty state was initialized to bypass the barrier.

SQLite comparisons generated sixteen WAL/SHM files alongside the 2,375 baseline files. [Accounting](../work/migration/initial/sidecar-accounting.json) distinguishes them from original assets. They were never generically deleted; capture uses the SQLite backup API and omits transient sidecar copies. The temporary browser-profile export remains isolated under work and was never published as an application.

## Original-installation protection

The original capture is `backups/pre-migration-20261111T191326Z`. It protects 2,699 original files, including the full nested release checkout, and 334 directories through the recorded topology supplement. Its file-only first rehearsal exposed missing empty Git directories; this failure was corrected before activation and remains part of the evidence history. [Independent restoration evidence](../work/recovery/original-rehearsal-verified/summary.json) proves exact file reconstruction, equal nested Git index/worktree status and dangling-object checks, and matching Home/French SQLite semantics. The private plaintext rehearsal was removed after comparison.

[Fresh baseline evidence](../work/migration/baseline-e7884df27349/baseline.json) contains SQLite backup API snapshots sealed with DPAPI plus file and semantic hashes. Neither report contains task text, private transcripts or credentials. Pin the original capture through acceptance and until a later compatible Verified recovery point exists.
