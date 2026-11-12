# Core

Read root guidance. Command service owns durable intent and operation ordering; supervisor alone owns process restart policy. Scheduler submits through commands. Acknowledgment follows commit. Stop/Disable/Quit override busy work and every launch rechecks intent. Bind loopback only; authenticate IPC before launcher tickets. Own essential tasks and fail readiness on supervision loss. Never conflate a listener, process creation, readiness and completed polling.
