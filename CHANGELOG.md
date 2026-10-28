# Changelog

- Keep the newest pre-edit backups by their creation timestamps rather than inherited source modification times, including when edits share a timestamp.

## 1.0.0 — refined 2026-10-27

First standalone Windows source release for long tasks using tool-capable local models. Supports LM Studio and local OpenAI-compatible streaming backends, core file/job tools, web and desktop capability groups, media verification, and optional background or live Blender control.

- Retain the original objective, checkpoints, tool receipts, uncertain-action records, and task history across restarts.
- Validate configuration, preserve locked state in bounded pending snapshots, and rotate logs and file backups.
- Track independent jobs with command/input identity, cancellation, deadlines, bounded output, and owned Windows process trees. Inspect uncertain work instead of replaying it.
- Add streaming and execution timeouts, reject malformed tool calls, load optional integrations lazily, and enforce active tool groups.
- Separate provider recovery from actor failures, preserve pinned models and instance identifiers, and bound recovery and no-progress retries.
- Verify actual deliverables and current visual evidence before completion; recognize external-project output changes and verified system actions.
- Make the watchdog the normal launch path, detect conflicting ports, and validate installation without requiring Administrator privileges.
- Simplify the local interface, show real task paths, recover missing task selections, and add practical starter briefs and setup guidance.
- Build a reproducible source-only archive with exact inventory, content hashes, checksums, and privacy checks. Add Windows CI and focused regression coverage.

- Detach noninteractive helper input from the tool protocol; fix headless Blender completion hanging on the MCP input pipe.
- Harden browser transfers with monitored limits, cancellation, unique atomic output commits, hashes, and driver/scratch cleanup.
- Prevent late task responses from replacing a new selection or draft. Reject invalid time budgets without partial task changes.
- Honor pause during model discovery/verification and retain follow-ups received at completion or shutdown. Archive result recovery sidecars.
- Bound malformed model streams, map advertised reasoning settings for the chat API, protect shared runtime files, and keep logs bounded when rotation is locked.
- Verify a fresh Python 3.12 environment, real browser download/UI races, and repeated noisy job cleanup alongside Python 3.14 validation.
- Keep each task's first selected model across recovery unless explicitly overridden; honor disabled automatic LM Studio startup/reload settings.
- Support 50-hour work within the 72-hour task, shell-job, and frame-render limits; finish verified work before the budget expires.
- Resolve job input identity against its working directory, reject delayed launches after the deadline, and publish terminal results after owned-process and pipe cleanup.
- Preserve completed job receipts on Stop, reject cancellation races that could report success, and discard exited descendants during long runs.
- Refuse frame resume without matching scene/settings evidence and prevent camera fitting from overwriting an existing Blender project.
- Replace the interface and local guide with a simpler monochrome workspace and document model compatibility, recovery, and storage limits.
- Enforce total model-response deadlines across stalled headers and bodies, and release completed SSE connections before another request.
- Preserve external tool argument names during schema compaction, recover from oversized optional schemas, and archive large tool responses with explicit notices.
- Gate image inputs and visual review on the selected model's capabilities; preserve text-only file and DOM workflows.
- Keep drafts with their task, bound displayed conversation entries, and ship dependency-free interface regression tests.
- Bound persistent live Blender diagnostics, tolerate transient diagnostic locks, and reclaim only confirmed-dead browser scratch owned by the same task.
- Pin CI actions, run hosted Windows builds with reduced privileges, and attach verified source packages to passing workflow runs.
- Add a shared first-file walkthrough, direct setup links, clearer controls and troubleshooting, and a starter example that preserves drafts and remembers the chosen time limit.
- Replace the first-file onboarding with direct installation and usage documentation, practical project presets, and a concise technical overview. Presets leave the selected time limit unchanged.
- Build and test as a restricted Windows user in CI, with owned desktop and process wrappers, explicit output capture, and regressions for token permissions and hosted process cleanup. Keep the normal-user guard and host policy intact.

Local validation includes a live model write/read/follow-up, supervisor crash recovery, responsive interface checks, a deterministic Blender/render/resume/video pipeline, and a small model-driven Blender scene resumed through verified completion. This is experimental software; see [release status](RELEASE-STATUS.md) for tested scope and remaining limitations.
