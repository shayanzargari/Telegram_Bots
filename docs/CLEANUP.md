# Controlled cleanup and retention

The authoritative file disposition is [disposition-ledger.json](disposition-ledger.json). It records exact old paths, component owner, source hash/bytes, target responsibility, class, disposition and unresolved conditions. Unresolved `checks/` and `.local-telegram/` material remains pinned until every actual fixture/tool/recovery consumer and distinct assertion has been accounted for.

Use [cleanup.py](../tools/cleanup/cleanup.py) with an exact small [cleanup ledger](cleanup-ledger.json). Each Eligible entry requires a resolved absolute path, owner/class/reason, checked consumers, preservation/reconstruction route, passing evidence, no active consumer/run and an exact current tree fingerprint. The tool validates the batch, rechecks each target immediately before mutation and rejects links or paths outside this workspace. It never generically deletes authoritative data, credentials, Git history, WAL/SHM or complete recovery sets.

```powershell
py -3.11 tools\cleanup\cleanup.py --ledger docs\cleanup-ledger.json
py -3.11 tools\cleanup\cleanup.py --ledger docs\cleanup-ledger.json --apply --limit 10
```

The failed initial encrypted capture at `backups/pre-migration-20261111T191238Z` was removed as one evidenced batch. Every one of its 2,312 privately decrypted members was covered by the Verified original capture, with no uncovered hashes. The failed outcome and [coverage proof](../work/cleanup/partial-capture-coverage.json) remain. This reclaimed 55,048,238 logical bytes; observed volume free space rose 59,269,120 bytes during the batch. Concurrent unrelated writers may affect that volume delta. Moving files to an archive does not reclaim space.

Old environments require passing final-path replacements and compatible retained recovery inputs before removal. Original launchers/instructions and unique nested Git work remain preserved. Date or lack of a static import alone is insufficient evidence of obsolescence.

After final 27-suite installed acceptance, completed activation, a new Verified recovery point and a fresh consumer/shortcut check, the exact old French `.venv-bot`, `.venv` and `node_modules` trees were removed. Their byte-compared replacements and compatible wheels/Node inputs remain pinned. This batch removed 88,905,660 logical bytes; observed volume free space increased 98,107,392 bytes. [Final gates](../work/cleanup/final-replacement-gates.json), [reconstruction](../work/cleanup/environment-reconstruction.json), and [recovery pins](../work/cleanup/recovery-input-pins.json) retain the proof. Combined with the earlier failed-capture removal, total logical removal is 143,953,898 bytes. No archive move is counted as reclaimed space.

[Footprint measurement](cleanup-measurements.json) includes the new environments, canonical state, protected backups and work. The recorded root total is 1,190,025,036 logical bytes, approximately 998,773,003 bytes above the original application/plan baseline; external original AppData is outside that baseline. Allocation and volume deltas have the recorded concurrent-writer caveat. Retaining verified recovery and unique historical outputs deliberately takes priority over minimizing footprint.

Routine test bulk uses [retention.py](../tools/cleanup/retention.py). It protects the latest three completed routine bundles per suite/environment, unresolved failures, the latest successful acceptance and explicit/current-document/reused-result references. Compact summaries/outcomes are permanent records even when redundant resolved-failure bulk expires.

```powershell
py -3.11 tools\cleanup\retention.py
py -3.11 tools\cleanup\retention.py --apply
```

The 512 MiB routine-output budget is soft and thirty days is a review threshold. Pins override both. The maintained four-run fixture includes a resolved failure and reused provenance, preserves outcomes/links, expires only redundant bulk and demonstrates a second bounded cycle. Cleanup introduces no background service, autostart or unrelated Windows changes.

The [two recorded routine cycles](../work/retention/routine-cycle-evidence.json) both Passed with matching relevant fingerprints and 8893 logical output bytes each; their summaries are explicitly pinned. [Applied retention](../work/cleanup/retention-final-applied.json) subsequently expired 16 redundant routine bundles and removed 120386 logical bytes while preserving permanent outcomes and required references/failure evidence. The routine bulk before that application was 177407661 logical bytes, within the soft budget. These historical measurements are separate from the four application/dependency removal entries and are not a new whole-root footprint measurement.
