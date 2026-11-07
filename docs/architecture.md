# Architecture

Task Continuity Agent separates durable supervision from model reasoning.

## Supervisor

`app/agent.py` owns the loopback UI, task lifecycle, deadlines, worker restarts, model discovery, and task-state persistence. It does not rely on one model conversation surviving for the duration of a task.

## Actor

`app/actor.py` runs a bounded local-model tool loop. The model sees the immutable objective, compact durable state, recent receipts, relevant task files, and only the optional capability group needed for the current phase.

## Tools

`app/tools.py` exposes normal-user file, web, browser, desktop, media, and optional Blender actions over a local MCP stdio server. Long operations are delegated to tracked jobs in `work_tools.py`.

## Durable state

Each task lives under `data/Tasks/<uuid>`. Atomic JSON writes and pending-write fallback preserve state across Windows file locks. Background jobs record PID, process creation time, arguments, output, status, and result so a restarted reasoning process can inspect instead of relaunching them.

## Progress

Progress is measured from durable output changes and active tracked jobs, not from the model's prose. Scratch scripts, logs, screenshots, backups, and supervisor metadata do not count as deliverable progress.

## Completion

`acceptance.py` pins the task identity and explicit output requirements. Completion validates required files and, for visual work, requires review evidence tied to current file hashes.

Clearly identified source files are excluded from output inference, and direct named-output requests are checked individually. Numerical data tasks require a successful tracked job that finishes after the current deliverables were saved. This establishes execution evidence, not independent proof that an arbitrary algorithm or answer is correct. The language classification remains heuristic.

For Python work, `run_python` passes the script and argument list directly to the installed Python environment through the job runner. Paths containing spaces and shell metacharacters remain literal. General commands continue to use `shell`.

## Capability groups

Core tools are always available. Web, desktop, media, Blender, and external MCP tools are opt-in groups. This keeps tool-schema/context overhead bounded for smaller local models and prevents Blender-specific machinery from polluting unrelated work.
