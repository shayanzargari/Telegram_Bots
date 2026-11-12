# Bot Control Center

Implement and maintain the local installation described in `docs/IMPLEMENTATION_PLAN.md`.
For substantial work read `docs/PROJECT_STATUS.md`, the relevant part of `docs/PROJECT_MAP.md`, and all applicable scoped AGENTS.md files before editing.

- Preserve keyed Home state and approved French content, ownership, positions, drafts, history and audio. Never initialize missing expected production state.
- Protect credentials with current-user Windows DPAPI; never print, stage or ordinarily archive plaintext credentials. Remotes and source publication require explicit user authorization; keep live/private state excluded. No unrelated startup or power changes.
- Production imports installed wheels. Shared runtime imports neither core nor applications. Bots do not import one another.
- Use isolated fixtures for faults, Telegram and browser checks. Record Passed/Failed/Blocked/Skipped/Reused evidence accurately.
- Follow the canonical plan's seven phases and cleanup gates. Update the sole checkpoint after milestones. Completion requires actual evidence, with external/phone limitations explicit.
- Coordinator owns shared manifests, integration decisions and checkpoint. Implementer owns scoped code; Verifier independently checks observed behavior. One agent may perform all roles. The plan authorizes supported subagents when useful; assign disjoint files and keep one coordinator.
- Project workflows live in `.agents/skills/`; consult the task-relevant skill and maintained tool rather than inventing parallel implementations.
