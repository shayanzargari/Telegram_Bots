# Development

Begin at PROJECT_STATUS.md and the relevant PROJECT_MAP section. Read root and scoped AGENTS.md before editing. Originals and recovery evidence remain excluded from active discovery; a historical copy must never override active instructions.

Recreate environments at final paths; do not move virtual environments. From the workspace root:

```powershell
py -3.11 tools\environments\prepare.py --all --locked
py -3.11 tools\testing\run_tests.py --profile smoke
py -3.11 tools\testing\run_tests.py --profile affected --changed-path core
```

The build tool records wheels, hashes, install reports and dependency snapshots under work/build. Production imports only installed wheels. Package source uses src layouts; shared runtime imports no applications or core, and neither bot imports the other. Tests must include installed imports from an unrelated CWD, assets and entry points after relocation/dependency changes.

Coordinator owns integration, shared manifests/catalogs and the checkpoint. Implementer owns assigned files. Verifier checks observable outcomes with isolated fixtures and retains original failures. One contributor may fill all roles. When using supported subagents, assign disjoint files and keep one coordinator.

Local workflows are .agents/skills/add-bot, verify-change and close-task. They call the maintained tools. Only the current checkpoint is authoritative; no competing MEMORY/HANDOFF trees. The user authorized source publication to [shayanzargari/Telegram_Bots](https://github.com/shayanzargari/Telegram_Bots) on 2026-11-12. Keep credentials, live/private data and generated artifacts excluded; inspect the reachable Git history before a push. Other remotes/publication need explicit authorization. Do not modify global Git identity, add unrelated startup entries or change power settings.

Preserve production state and approved French wording/order. Before a migration, refresh writers/fingerprints, obtain exclusive maintenance access, seal and rehearse recovery, stage and compare, then use the durable activation journal. A missing expected store or incomplete journal blocks writable startup. Fault tests never corrupt, fill or interrupt the production filesystem.
