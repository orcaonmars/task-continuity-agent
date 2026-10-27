# Task Continuity Agent

[![Validate](https://github.com/orcaonmars/task-continuity-agent/actions/workflows/validate.yml/badge.svg)](https://github.com/orcaonmars/task-continuity-agent/actions/workflows/validate.yml)

**Give your local AI a task worth finishing.**

A Windows companion for tool-capable local models that work across files, the web, desktop apps, and optional Blender. Set a time budget, describe the deliverables, and let the agent implement, inspect, correct, and checkpoint its work.

The priority is reliability over long runs, followed by effectiveness and simplicity. This is an experimental source release; recovery mechanisms do not guarantee that every task will succeed unattended.

![The local agent interface](docs/images/interface.png)

## Why use it?

- **Pick up where work stopped.** The original brief, checkpoints, recent tool results, and outputs stay on disk across model and supervisor restarts.
- **Keep long jobs independent.** Builds, conversions, and renders have durable job names, process identities, logs, deadlines, and results.
- **Judge real progress.** Repeated planning, helper scripts, and logs do not count as completed deliverables.
- **Verify before finishing.** File checks, current image hashes, and final-video samples help catch premature completion.
- **Keep the model focused.** Core tools stay available; web, desktop, media, Blender, and extra MCP tools use small capability groups.
- **Recover with limits.** A watchdog restarts the supervisor; model recovery and no-progress retries have bounds.

## Get started

You need Windows 10/11, Python 3.11 or later, and a tool-capable local model server. A vision-capable model is needed for meaningful visual refinement. Models and LM Studio are installed separately.

Extract or clone this folder, open PowerShell in it, and run normally as your signed-in user:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-agent.ps1
```

The installer creates an isolated `.venv`, checks dependencies and tests, creates `data/config.json`, and adds a Desktop shortcut. The normal launcher uses the watchdog and opens the interface after checking this installation is ready.

The default provider is LM Studio at `http://127.0.0.1:1234/v1`. Load a model with tool use enabled. With `preferred_model` empty, a new task selects the first loaded LLM, then keeps that choice across restarts. A missing selected model produces an explicit error. Set `preferred_model` to make an explicit selection or change it later.

The default interface is `http://127.0.0.1:8765/`. If that port belongs to another app or agent installation, choose another `ui_port` in `data/config.json`. Closing the browser tab leaves the agent running. **Pause** stops reasoning while existing jobs may finish; **Stop** cancels the task and its owned jobs. **Resume** uses saved context and starts a fresh budget from now.

Choose a budget from **0.1 to 72 hours**, including **50 hours** for longer work. It is a maximum: verified work ends early. Pausing does not suspend the existing deadline.

## A useful overnight brief

Choose a time budget in the interface, then adapt this brief:

> Build a small responsive website for a local repair shop in the task folder. Include services, opening hours, and a contact section using clearly marked sample details. Keep the source editable and avoid external services. Build the complete first version, check it at desktop and mobile sizes, fix visible and functional defects, and checkpoint each milestone. Save a README with run instructions and verification evidence. Finish when the result is checked and list remaining limitations. Do not publish anything.

For existing code projects, reports, file work, and Blender, see [example tasks](docs/example-tasks.md). Give a specific work folder, outputs, success criteria, and boundaries. Start with a short recoverable task before leaving a larger task overnight.

## Capabilities and setup

| Capability | What it enables | Extra setup |
| --- | --- | --- |
| Core | Text files, tracked PowerShell jobs, briefs, checkpoints, completion | Installed Python runtime |
| Web | Search, HTTP downloads, persistent visible browser | Chrome by default; `browser_channel: "msedge"` selects installed Edge |
| Desktop | Screenshots, accessibility inspection, normal-user input | Windows signed in and unlocked |
| Media | Image inspection, contact sheets, video encoding and review | ffmpeg and ffprobe on PATH for video |
| Blender | Isolated project jobs, previews, resumable frames, optional live control | Blender; live control also needs a compatible add-on |
| Extra | User-configured MCP servers | Review and install each server separately |

Blender is disabled by default. Set `capabilities.blender` to `true` in `data/config.json`; set `blender_exe` if automatic discovery cannot find it. Background Blender work needs no bridge add-on. See [Blender setup](docs/blender.md).

For another local backend, set `provider` to `"openai-compatible"` and point `api_base` at its `/v1` endpoint. It must support model discovery, streaming chat completions, and tool calls; compatibility is not universal across models or servers. Set `vision: true` only if that model/backend accepts image inputs. Text-only models keep file, DOM, accessibility, and background-job tools; images are saved for inspection by a capable model and cannot count as its visual review. Backend-specific request fields belong in `request_overrides`. No cloud API keys are bundled or required. Set `auto_model_recovery: false` to prevent automatic LM Studio server startup and model reloads when sharing it with another application. See [advanced configuration](docs/configuration.md) for defaults and recovery settings.

## Before an unattended run

Run `scripts/doctor.ps1`. It checks configuration, runtime, model endpoint, disk space, and optional capabilities. Keep the computer powered; active work requests wakefulness but cannot keep processes alive through power loss or Windows shutdown. Desktop interaction needs an unlocked session.

Task state lives under `data/Tasks/<uuid>`. Older conversations are archived, file-tool edits retain bounded backups, and tracked job/event logs rotate. Outputs, downloads, and context archives are retained for recovery; these limits are not a total disk quota. Remove old tasks yourself when no longer needed. `max_download_mb` limits each direct HTTP and requested browser download; browser monitoring can briefly overshoot while cancellation takes effect. Browser downloads use unique filenames and verify before committing.

Edits to an explicitly selected external project are supported. Include changed files as checkpoint deliverables so the progress detector can watch them, and report their actual paths at completion.

## Reliability and limits

Regression coverage and local checks exercise model-driven file work, follow-ups, supervisor recovery, desktop/mobile layout, and background job/render/video workflows. See [RELEASE-STATUS.md](RELEASE-STATUS.md) for current evidence and genuine limits.

This release has **not** undergone a full overnight soak test or an extended artistic Blender refinement task. Model visual judgment is subjective; structural checks cannot prove that a scene is perfect or arbitrary code is correct. Add-on compatibility, changing websites, GPU memory pressure, and login/CAPTCHA can still interrupt work.

The agent runs with your Windows account's permissions. It is not a security sandbox. Use backups and recoverable work; capability switches do not sandbox arbitrary shell commands. Login, purchases, publishing, external messages, and privilege escalation remain human-authorized actions. See [SECURITY.md](SECURITY.md).

## Develop and release

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

The build validates source, dependencies, tests, configuration, PowerShell syntax, and privacy checks. It creates a reproducible source-only ZIP, checksum, and per-file manifest in `dist/`, then verifies archive contents against the source allowlist. Runtime data, browser profiles, models, and `.venv` are excluded.

CI is configured to build on Windows with Python 3.11, 3.12, and 3.14. Hosted CI results must be checked after publication. See [architecture](docs/architecture.md), [contributing](CONTRIBUTING.md), and [publishing](docs/publishing.md).

Node.js 18+ runs the optional interface regression tests through the same validation command. The application itself does not need Node.js. CI installs Node.js 22 so these checks always run there. Passing Python 3.12 CI builds also expose the verified source ZIP, checksum, and manifest as a workflow artifact.

## Help test it

The most useful feedback is a reproducible task on a different Windows machine or model backend. Start with recoverable files, record what worked and what failed, and remove private data before opening an issue. See the [benchmark protocol](docs/benchmarking.md) and [roadmap](docs/roadmap.md). Setup problems, uncertain recovery, and duplicate actions take priority over new integrations.

## License

MIT. Contributions that improve recovery, verification, and clarity are welcome.
