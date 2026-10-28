# Reliability engineering notes

Task Continuity Agent is built around one uncomfortable fact: a model saying it made progress is not evidence that the requested output changed.

The supervisor therefore treats the model as a reasoning component inside a more durable execution system.

## Failure modes addressed

- **Context erosion:** long conversations and large tool schemas crowd out the original objective.
- **False progress:** helper scripts, logs, repeated inspections, and planning prose can look busy without changing the deliverable.
- **Duplicate side effects:** a timeout or process restart can leave uncertainty about whether a write or launch already happened.
- **Lost long jobs:** renders, builds, conversions, or other subprocesses can outlive a reasoning request.
- **Stale assumptions:** files, images, application state, and process state can change after the model last observed them.
- **Premature completion:** a model can claim success before required files, tests, previews, or verification exist.
- **Provider failure:** the local inference server can fail independently of the task and its background jobs.
- **Windows file locking:** state files can temporarily resist atomic replacement.

## Core design

### Immutable task identity

The original human objective is pinned to disk and hashed into the acceptance contract. Checkpoints and model summaries cannot silently replace it.

### Bounded active context

The actor keeps recent tool-call/result groups and compact receipts in context. Older complete interaction groups are archived to disk instead of being rewritten into a lossy model-authored summary.

### Capability routing

Core file, shell, job, checkpoint, and completion tools remain available. Web, desktop, media, Blender, and external MCP tools are enabled only when needed, reducing schema overhead and unrelated failure surfaces.

### Durable jobs

Long commands run through tracked job folders containing immutable arguments, PID plus process-creation-time identity, logs, and a durable result. A restarted actor inspects an existing job instead of blindly relaunching it.

### Idempotency and uncertain writes

Job names are bound to their original command. A changed retry requires a new name. Interrupted tool turns are recovered with an explicit “may have executed” receipt so the model must inspect actual state before repeating a write.

### Independent progress accounting

For action tasks, the stall detector watches durable output changes and real live jobs. Supervisor metadata, logs, backups, screenshots, context archives, and helper scripts do not count as production progress.

### Verification gates

Completion checks required output extensions and current file existence. Visual work additionally requires review evidence tied to current hashes. Videos are probed and sampled from the final encoded file. Blender projects are opened headlessly for structural verification when Blender is part of the contract.

### Windows-safe state persistence

State uses write-flush-fsync-replace semantics. If Windows temporarily locks the target, the newest state is preserved in a pending sidecar and reads prefer the newest valid copy. A later successful atomic write cleans stale sidecars.

### Task-relative paths

Relative model/tool paths resolve against the current task folder, never against the launcher or MCP server working directory. Absolute paths remain available when the user explicitly targets another location.

## Current validation

The regression suite covers parsing and acceptance, real Windows locked-file fallback, bounded pending state and logs, external deliverable progress, configuration failures, malformed tool streams, active capabilities, uncertain actions, independent jobs through real MCP owner crashes, cancellation, deadlines, process-tree ownership, model selection and recovery identity, clipboard restoration, and exact release packaging. See the current release status for the latest executed count and environment.

Live local checks exercised model write/read/completion and follow-up, watchdog crash recovery with retained results, UI authentication and narrow-screen layout, and an isolated Blender 5.0.1 background render/resume/video pipeline. [Release status](../RELEASE-STATUS.md) records the scope, limits, and the maintainer's subsequently reported overnight endurance run. A small model-driven scene also resumed through verified completion; extended artistic quality remains unverified.

## Additional edge cases

A live model-driven Blender check exposed inherited tool-protocol input blocking headless final validation. Noninteractive helper processes now use an empty input stream; an open-parent-pipe regression exercises that boundary.

The second audit covers controls arriving during discovery or completion, late UI responses, drafts edited during pending submissions, pending-only result archival, shared runtime file protection, bracketed backup names, browser cancellation/cleanup, model response byte limits, and native-versus-chat reasoning settings. Logs remain bounded even when another process temporarily prevents rotation; diagnostic data may be dropped while task state continues to persist independently.

## Limits

These mechanisms reduce predictable orchestration failures; they do not make a local model infallible. Complex UI state, third-party site changes, memory pressure, model mistakes, login/CAPTCHA, and subjective quality can still require human intervention.

Use recoverable work, keep backups, and treat third-party MCP servers as code with the same permissions as the signed-in user.
