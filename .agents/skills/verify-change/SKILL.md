---
name: verify-change
description: Select and run deduplicated, isolated checks for a bounded Bot Control Center change using its maintained test coordinator and relevant fingerprints.
---

Read root/scoped guidance, docs/PROJECT_STATUS.md and [testing](../../../docs/TESTING.md). Identify changed components and whether source, installed wheels, dependencies, assets, contracts or platform behavior changed.

Use tools/testing/run_tests.py --list, then --profile affected --changed-path PATH or explicit suite IDs. Documentation-only work normally uses docs-context; shared changes include actual consumers; lifecycle/security/UI changes include their native Windows/browser suites. Rebuild installed wheels before claiming installed verification. Keep synthetic state, credentials, ports and identities isolated.

Do not rerun unrelated heavy suites without a failure, change or unresolved concern. Only reuse results with matching relevant fingerprints and label them Reused evidence. Preserve original failure outcomes and meaningful reproducers. Report Passed, Failed, Blocked, Skipped, Not applicable and Reused evidence truthfully; local tests cannot establish real Telegram or phone behavior.

Completion: link the coordinator summary and source/build/environment identity, explain failed or blocked gates, and update the checkpoint's actual verified scope. Do not invent another test runner.
