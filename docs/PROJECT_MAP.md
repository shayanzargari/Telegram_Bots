# Project map

The canonical requirements are in IMPLEMENTATION_PLAN.md. PROJECT_STATUS.md is the current checkpoint; DECISIONS.md records material choices.

Core management: core/ (commands, supervisor, API/UI, schedules, registry, recovery).
Shared contracts and Windows ownership/security: shared/src/bot_center_runtime/.
Home code: bots/home/src/home_bot/. French code: bots/french/src/french_notes/.
Configuration and catalogs: config/. Production state/content: data/. Encrypted credentials: .secrets/.
Supported entry points: START.cmd/START.vbs, launchers/, and installed console/module entry points.
Maintained development/recovery tools: tools/. Verification: tests/ and config/suites.json.
Builds and isolated outputs: work/. Protected recovery: backups/. Pinned inactive originals: archive/.

Maintained commands (Python 3.11): tools/environments/prepare.py --all --locked builds wheels and installs final environments; tools/testing/run_tests.py --list/--profile acceptance --fresh selects isolated suites; --profile live separately observes real polling without sending messages. tools/installation/activate.py --prepare/--apply publishes the initial generation only after matching local acceptance. tools/bots/manage_bot.py is the onboarding adapter; tools/recovery/recovery.py owns offline capture/verify/restore; tools/cleanup/cleanup.py and retention.py own ledger-backed cleanup and retained evidence.

Runtime controls use .venvs/core/Scripts/python.exe -m bot_center --root ROOT followed by status/start/stop/restart/retry/enable/disable/start_all/stop_all/job/backup/open_editor/quit. Root launchers use the same protected IPC startup. Independent standalone launchers live under launchers/home and launchers/french. French editor uses its separate installed environment and lifetime.

Authoritative Home state after activation is data/home/tasks.sqlite3 plus its saved exports/configuration. French accepted content is data/french/notes; reader state/audio is data/french/reader; editor drafts/history/profile are data/french/editor. Canonical history is shared through the manifest's history_path. config/activation-journal.json blocks incomplete publication. data/core/core.sqlite3 owns all intent and operations; config/bots/*.json declares capabilities only.

Coordinator owns shared catalogs, integration choices and PROJECT_STATUS; implementers own assigned source; verifiers inspect observed behavior and evidence. Original application directories are retained preservation/compatibility material until their individual ledger gates permit retirement; they do not replace installed production packages.
