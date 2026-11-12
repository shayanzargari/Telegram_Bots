# Native Windows acceptance

The installed windows.runtime suite passed **29 tests** within [27/27 final local acceptance](../work/tests/20261111T221408Z-146cc5ca/summary.json). Build identity: `21db105f122078f02f847712775c5d6f28a3a2a75016432e79d9a6339937e566`. All native fixtures use isolated roots, fake bot identities and owned processes.

Observed: current-user DPAPI/tamper/rotation/private ACLs; numeric locks in both launch orders and caller-thread exit; suspended creation/Job assignment/resume; kill-on-close children/grandchildren; explicit handle inheritance; protocol size/schema/run/sequence validation; output-cut redaction and responsive control; protected local IPC, 100 roundtrips, restricted-token denial and unrelated-listener rejection; actual concurrent launcher/reopen/saved-Stop/external-hold flows; four partial-bootstrap failure paths.

Actual independent French editor/browser relocation and reader/core shutdown/crash/task-loss passed in french.editor-lifetime. These remain distinct from primitive checks. Restricted-token tests did not create or sign into a second Windows profile.

Original failures remain in [the first full acceptance](../work/tests/20261111T215907Z-789c76e9/summary.json) and retained work/native records. The broken-pipe fixture correction accounted for the Windows venv redirector retaining a duplicate read handle; production cleanup was independently exercised by the other fault paths. Prior record details remain in local Git history.

[Independent deployed status](../work/live/independent-status.json) confirms a ready core and distinct ready/Connected bot processes. It sends no Telegram messages. Inbound replies and phone checks remain unverified; the user is testing them as of 2026-11-12. The [documentation closure](../work/context/closure-20261112.json) records current read-only status and distinguishes fresh documentation checks from unchanged runtime evidence. [Complete acceptance map](ACCEPTANCE.md).
