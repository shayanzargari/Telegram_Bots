---
name: add-bot
description: Create a Python bot or register an existing project in this Bot Control Center using the maintained onboarding service, leaving it disabled and stopped.
---

Read root guidance, docs/PROJECT_STATUS.md, and [onboarding](../../../docs/ADDING_A_BOT.md). Read the existing project's own instructions before adaptation.

Use tools/bots/manage_bot.py and the installed registration service; templates/python_bot is the sole template. Validate a meaningful Windows-safe ID, canonical paths, contract version, expected numeric Telegram identity, requested capabilities and existing state. Never overwrite an existing project or initialize missing expected state. Keep credentials in masked local setup; no tokens in chat, manifests or command arguments.

Use isolated fake transports for offline checks, then the coordinator's affected/shared contracts. Record template/runtime/wheel identities and actual catalog/map publication. Registration remains disabled/stopped; Enable leaves stopped, and Start requires a separate explicit management action.

Completion: report created/registered paths, package/environment identity, checks, unresolved identity/credential gates, and disabled/stopped state. Update the sole checkpoint. Do not alter Home/French business behavior to add a compliant bot.
