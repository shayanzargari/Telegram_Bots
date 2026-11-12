# Operations

Open START.vbs or START.cmd. Repeated opening authenticates the current core over the private named pipe before exchanging a one-use browser ticket. A port collision never authorizes terminating the listener or handing it a ticket. Use logs/core/core.log if startup fails.

Bot status distinguishes Running (process), Ready (validated initialization), Connected (recent completed poll) and Unknown/Unresponsive. A network outage reconnects in the worker; a terminal credential/identity/configuration/conflict problem requires attention. Unexpected process failures retry after 2,5,15,30 seconds; the fifth within ten minutes requires attention. Retry resets the budget while retaining diagnostics.

Management commands use the installed core environment and protected IPC. They exit after durable submission, while the persistent core retains ownership:

```powershell
.venvs\core\Scripts\python.exe -m bot_center --root "D:\telegram bots" start-core
.venvs\core\Scripts\python.exe -m bot_center --root "D:\telegram bots" status
.venvs\core\Scripts\python.exe -m bot_center --root "D:\telegram bots" stop --bot home
.venvs\core\Scripts\python.exe -m bot_center --root "D:\telegram bots" restart --bot french
.venvs\core\Scripts\python.exe -m bot_center --root "D:\telegram bots" job --bot french --job validate_audio
.venvs\core\Scripts\python.exe -m bot_center --root "D:\telegram bots" backup
.venvs\core\Scripts\python.exe -m bot_center --root "D:\telegram bots" quit
```

Use an explicit --operation-id to safely repeat an uncertain submission; changing its target/action/payload is rejected. Submitted/queued requests are not Accepted until committed. During capture, controls wait; priority Stop/Disable/Quit commits before capture barriers reopen.

Supported standalone launchers under launchers/home and launchers/french keep a lifetime-owning runner alive and use the same numeric identity lock/canonical resolver. They do not rewrite core intent. Stop a standalone runner in its own console. Core Stop controls owned processes only. After an outside instance exits, explicitly choose Start; the takeover hold survives core restart.

French editor opens only on request and reuses its single-writer session. Reader Stop/Restart and Quit core leave it usable. Closing the browser affects no bot intent. PC sleep/shutdown and absent core interrupt service. No autostart or power policy changes are made by this installation.

Retry preserves the saved desired state; retrying a deliberately stopped bot leaves it stopped. Maintenance deadlines request cancellation, and another conflicting launch waits for the old worker to drain. After an abrupt interruption during a Telegram send, delivery may be unknown; uncertain text sends are not blindly replayed. Send a fresh /menu to recover the visible panel.

Manual restore is an exclusive offline operation. Close the editor normally, quit the core and standalone runners, validate compatibility and preserve current state first. Recovery starts bots stopped and schedules disabled; explicit resumption is required. See RECOVERY.md for the maintained commands.
