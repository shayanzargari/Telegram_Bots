# Refreshed installation inventory

The first refreshed inventory is [work/inventory/files.json](../work/inventory/files.json), with 11,317 file fingerprints across the two applications and their dedicated AppData state. [The exact disposition ledger](disposition-ledger.json) owns individual paths and targets; it does not copy private record values.

Original logical bytes: Home 36,483,623; French 154,705,843; reader AppData 23,551,589; editor AppData 49,885,734. These values are the current implementation baseline, not the historical counts in the supplied plan. Initial D: free space was approximately 3.5 GB. Byte totals are logical sizes; allocation is measured separately when supported.

Both application directories are ordinary directories, with no root junction. No relevant Python application writer was observed before protected capture; unrelated radar Python processes remain outside scope. Source paths and browser/editor state must be refreshed again at activation because legitimate concurrent user changes cannot be overwritten.

The nested `Our_Home_Telegram_Bot/.publish/home_shopping` checkout has an unborn `main` branch, a populated index/working tree, ignored material and dangling Git objects. Its entire `.git` tree, ignored files and directory topology were captured with current-user DPAPI and independently restored. No remote was added or contacted. See [recovery evidence](../work/recovery/original-rehearsal-verified/summary.json).

Home authoritative state is `data/tasks.sqlite3` with complete keyed tasks/members/meta and its exports/backups. A fresh semantic baseline found 38 tasks, 2 members and 74 meta entries; these are observations, not hard-coded expected production counts. The reader database includes ownership/invitations/preferences/positions/sessions/processed updates and accepted snapshots. The editor profile contains drafts/history/backups/audio and dedicated application browser state. [Fresh consistent baseline](../work/migration/baseline-e7884df27349/baseline.json) uses SQLite backup API snapshots rather than copying live main databases alone.

The current-user Startup folder contained unrelated Rainmeter/OneNote entries; the current-user Run key contained unrelated WeLink. No Home/French-owned scheduled task or startup trigger was found in the initial refresh. No unrelated entry or Windows power setting was changed. Original Home autostart scripts remain recovery/compatibility material and are not executed for reorganization.

Python 3.11.9 is available alongside unrelated 3.13 and 3.9 installations. The old French reader/editor environments record Python 3.11.9; final environments are recreated under `.venvs/` rather than moved. Browser QA has an independent Node dependency lock. Existing Git user identity was available; no global settings change is required.

Consumed historical fixture dependencies were found under French `checks/` (editor-update notes and current-work markers), plus Home tests that wrote `.local-telegram/` reports. Those inputs are being extracted into owned fixtures and output routing is moved into isolated work directories before any historical bulk is eligible for cleanup. Old dates are not evidence of obsolete content.
