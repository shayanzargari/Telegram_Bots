# Verification

[run_tests.py](../tools/testing/run_tests.py) is the standard-library coordinator. [The suite catalog](../config/suites.json) owns stable IDs, commands, environments, affected inputs, resources, platform, timeout and category. [The operation catalog](../config/operations.json) distinguishes production jobs, developer tools and historical migrations.

Run the coordinator by absolute path from any directory. It resolves the installation from its own file, invokes direct executable argument lists and uses declared `.venvs/<environment>` interpreters. Production and installed-build probes never require editable installs, `sys.path` edits or `PYTHONPATH`.

```powershell
py -3.11 tools\testing\run_tests.py --list
py -3.11 tools\testing\run_tests.py --profile smoke
py -3.11 tools\testing\run_tests.py --suite home.unit --suite french.unit
py -3.11 tools\testing\run_tests.py --profile affected --changed-path bots\home
py -3.11 tools\testing\run_tests.py --profile shared-compatibility
py -3.11 tools\testing\run_tests.py --profile docs-context
py -3.11 tools\testing\run_tests.py --profile integration --profile windows --profile ui
py -3.11 tools\testing\run_tests.py --profile acceptance --fresh
```

Repeated suite IDs across profiles are deduplicated. Documentation changes select context checks; shared changes select implementation checks plus all installed consumers. Component unit suites preserve existing assertions. Native Windows containment/DPAPI/pipe/launcher checks and real browser flows remain distinct suites. Live Telegram and user phone verification have separate outcomes; offline success never implies either.

onboarding.generated creates a new installed wheel and independent environment, registers it disabled/stopped, verifies unrelated-directory imports and runs its six fake contracts through this same coordinator. live.polling is selected only by --profile live after activation; it observes both real pollers through authenticated IPC and sends no messages. It does not establish inbound reply or phone acceptance.

Each run writes `work/tests/<run-id>/<suite-id>/...` and a compact `summary.json`. `work/tests/index.json` retains outcomes and provenance. Inputs include source/configuration/harness hashes, interpreter version, installed distribution versions, suite definition and platform. Successful evidence is reused only with explicit `--reuse`, an equal fingerprint and **Reused evidence** labeling. `--fresh` prevents reuse.

Outcomes are **Passed**, **Failed**, **Blocked**, **Skipped**, **Not applicable** and **Reused evidence**. Missing environments are Blocked, not passing empty checks. Original failures remain visible after later success. Empty/missing unittest directories are not accepted as a replacement for required scenarios.

Offline suites strip inherited credential variables and receive unique output/temp roots. They use fake transports/identities and never regenerate production audio. Named resources serialize browser/desktop/audio/live/performance work. Fault injection affects isolated fixture storage only. Per-stream logs are redacted before writing and capped at approximately 1 MiB; excessive output is drained without blocking control.

Initial recovery/coordinator results: [23 tests passed](../work/tests/20261111T193052Z-4f308aca/summary.json). Earlier manual test runs exposed Windows short/long temporary-path mismatches and test connections that committed without closing SQLite handles; the fixtures were corrected before the coordinator pass. These were fixture failures, not production state changes.

Retention is defined in [retention.json](../config/retention.json): latest three routine bundles per suite/environment, latest successful acceptance, unresolved failures and explicit pins. Compact outcomes remain. Referenced/reused evidence and required reproducers override routine caps. See [cleanup](CLEANUP.md).
