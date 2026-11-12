# Bot Control Center

A local Windows home for Home and French, with an independent French editor.

This source repository contains the application code, tests, maintained tools and documentation. Credentials, production databases, French note content, drafts/history/audio, local environments, backups and generated evidence stay on the local installation. Links into `work/` and `backups/` in the installation records resolve only there. A fresh clone requires environment preparation and separately restored application state before Home/French can run; missing production state deliberately blocks startup. See [development](docs/DEVELOPMENT.md) and [recovery](docs/RECOVERY.md).

Double-click **START.vbs** to open the authenticated dashboard. **START.cmd** is the console fallback. Both use the same installed launcher. Repeated clicks reopen the current core; closing the browser leaves managed bots running. Stop and Disable are saved before acknowledgment. A later ordinary launch preserves deliberate stops.

The dashboard shows process state, polling connection, uptime, diagnostics and operation outcomes. Accepted means the request is saved; Completed means the requested observed result was reached. Start all acts on eligible enabled bots and reports blocked ones. Enable leaves a bot stopped. Resolve terminal problems and use Retry for a new retry budget.

Use **Open French editor** when needed. Its independent process and dedicated profile survive reader controls and core shutdown. Save/close it before an exclusive backup or restore; never force-close drafts to obtain a capture.

Credentials stay in current-user Windows DPAPI files under .secrets. The French token is entered through the local masked setup command, never through chat or the web dashboard. The expected numeric Telegram identity is checked before polling or application-side mutations. A standalone owner appears as Running outside core; after it exits, Start is required explicitly before takeover.

- [Current implementation and verification status](docs/PROJECT_STATUS.md)
- [Project map and ownership](docs/PROJECT_MAP.md)
- [Everyday operations and commands](docs/OPERATIONS.md)
- [Setup and development](docs/DEVELOPMENT.md)
- [Backups and stopped recovery](docs/RECOVERY.md)
- [Verification](docs/TESTING.md)
- [Acceptance evidence and limits](docs/ACCEPTANCE.md)
- [Add a bot](docs/ADDING_A_BOT.md)
- [Scheduling](docs/SCHEDULING.md)
- [Cleanup and retention](docs/CLEANUP.md)
- [Authoritative plan](docs/IMPLEMENTATION_PLAN.md)

Python 3.11 is required. Production uses installed wheels in separate final-path environments, local UI assets and one loopback FastAPI/Uvicorn core. Node is used only for browser QA. No Windows autostart entries or power settings are added.

The PC and core must remain awake/running for service. Telegram retains pending updates for at most 24 hours; this installation preserves queued updates on ordinary recovery, but cannot recover updates Telegram has expired. Ambiguous remote sends cannot be guaranteed exactly once. [Telegram Bot API](https://core.telegram.org/bots/api#getupdates)

Backups on this drive provide rollback, not protection against drive loss. A separate destination can be selected manually. Current-user DPAPI credentials may need re-entry after a PC/profile change. See the recovery runbook.


Transfer format: every repository file is below 1 MB. The disposition ledger is stored in numbered parts. After cloning, run `python restore_split_files.py` to reconstruct it with SHA-256 verification. Use `python restore_split_files.py --check` for a read-only integrity check. See [transfer manifest](TRANSFER_MANIFEST.json). The local installation keeps its original ledger unchanged.
