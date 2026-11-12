# Adding a bot

Use Add bot in the authenticated dashboard for a new project or an existing managed-contract project. Registration, validation and Enable do not start it. After offline checks, import credentials through the masked local credential command, verify the expected numeric Telegram ID, explicitly Enable, then Start.

The dashboard and `tools/bots/manage_bot.py` use the same installed `RegistrationService`. Run CLI mutations with the core stopped; inspection and validation are read-only. Use the final core environment from any working directory:

```powershell
& 'D:\telegram bots\.venvs\core\Scripts\python.exe' 'D:\telegram bots\tools\bots\manage_bot.py' create garden --name 'Garden reminders' --telegram-id 123456789
& 'D:\telegram bots\.venvs\core\Scripts\python.exe' 'D:\telegram bots\tools\bots\manage_bot.py' inspect 'D:\telegram bots\work\garden-manifest.json'
& 'D:\telegram bots\.venvs\core\Scripts\python.exe' 'D:\telegram bots\tools\bots\manage_bot.py' register 'D:\telegram bots\work\garden-manifest.json'
```

Use a real intended numeric bot identity when known; omission leaves the project unverified and unable to poll. Never place a token in the manifest or arguments. `create --stage-only` renders preserved source without registering or initializing state; it returns its proposed manifest. Complete its environment and explicit state preparation, then use Register. Normal Create builds a wheel, installs the matching shared runtime separately into `.venvs/<id>`, initializes its new namespace once, runs fake-update contracts, and publishes the registry/catalog/map records through a durable registration journal.

If the numeric identity was omitted, Quit core and declare it later with `manage_bot.py set-identity <id> --telegram-id <number>`. This recoverably fills only a missing identity and checks duplicate aliases; it cannot replace a different previously expected bot. Then run the masked local credential command, whose online identity check must match that declaration. The declaration alone proves no Telegram connection and starts no process. `create --capability polling` or `--capability maintenance` limits the declared capabilities; the template never polls when polling was omitted.

`templates/python_bot/` is the sole template source (template/runtime 1.0.0, managed contract 1). A project receives `bots/<id>/src/<package>/`, pyproject, README, scoped rules, exclusions, independent environment, explicit expected-state marker/database, versioned manifest and applicable fake-update tests. Its declared capabilities include only those requested. The default polling template durably consumes local updates without adding Home/French business behavior. Extend its own `apply_local_update` and database schema deliberately; do not add supervisor special cases.

Existing projects must already have an installed Python environment, expected state, an importable package/module, instructions/dependency inputs and tests. Inspect and adapt their actual behavior first. Registration validates and fingerprints that source; it never replaces source/rules/lockfiles with defaults or initializes missing expected state. Runtime paths must be dedicated unlinked namespaces inside this installation; move/adapt external projects through a separately protected relocation before registration.

The manifest declares `schema_version`, `contract_version`, `id`, `display_name`, `package`, `module`, `python`, `argv`, `cwd`, `data_dir`, `log_dir`, `project_dir`, `credential_ref`, optional numeric `telegram_id`, `capabilities`, `jobs`, and `required_state`. `argv` uses the installed shared runner, never shell command text. Names reject Windows reserved forms, trailing spaces/dots, path traversal, case/normalization collisions and duplicate numeric Telegram identities. Enabled/desired state belongs only to the core database.

Interrupted publication remains in `data/core/registrations/<operation>.json`. Read it before retrying. `manage_bot.py recover <operation> --complete` completes prepared publication idempotently; omitting `--complete` restores recorded prior configuration after checking for newer changes. Generated source/environment/state are preserved for review. No recovery path starts the bot. Resolve interrupted creation/build evidence before reusing its namespace.

Run `tools/testing/run_tests.py --suite <id>.contract` after future changes, and shared compatibility checks when the runtime changes. Online identity, both live bots and phone checks are separate from offline fake transport evidence.
