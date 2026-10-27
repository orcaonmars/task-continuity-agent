# Task Continuity Agent

[![Validate](https://github.com/orcaonmars/task-continuity-agent/actions/workflows/validate.yml/badge.svg)](https://github.com/orcaonmars/task-continuity-agent/actions/workflows/validate.yml)

**Give a local AI a task it can pick up again.**

Task Continuity Agent is a Windows app for work that takes more than one step: editing files, improving a project, researching a question, or making a Blender scene. You describe the result and set a time limit. The agent uses tools, checks its work, and saves enough progress to continue after an interruption.

Created by [orcaonmars](https://github.com/orcaonmars).

This is an experimental source release. Recovery is a priority, but a long time budget is not a guarantee that a task will succeed unattended.

![The local agent interface](docs/images/interface.png)

## What it does

- Saves the original brief, progress, outputs, and recent tool results across model and supervisor restarts.
- Tracks long-running builds, conversions, and renders separately, so restarting the model does not require starting the job again.
- Checks actual deliverables before accepting completion. Logs and repeated planning do not count as a finished result.
- Loads the tools needed for the next step, including optional web, desktop, media, and Blender tools.
- Bounds recovery attempts and reports a blocker when it cannot keep making progress.

## Start with one small task

You need **Windows 10/11**, **[Python 3.11 or later](https://www.python.org/downloads/windows/)**, and a **local model that supports tool use**. Tool use means the model can ask the app to read a file or run an action, rather than only tell you how. Models and [LM Studio](https://lmstudio.ai/) are installed separately. Visual review also needs a model that accepts images.

Extract or clone this project into a writable folder. Open PowerShell in that folder and run as your normal Windows user:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

The installer downloads dependencies into an isolated `.venv`, checks the installation, creates `data/config.json`, and adds a Desktop shortcut.

In LM Studio, load a tool-capable model, open **Developer**, and switch on **Start server**. The default address is `http://127.0.0.1:1234/v1`; [LM Studio's server guide](https://lmstudio.ai/docs/developer/core/server) shows the controls. Then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-agent.ps1
```

The app opens in your browser, normally at `http://127.0.0.1:8765/`. Select **New task**, set **Time limit** to **0.1 hours** (six minutes maximum), and send:

> Create welcome.txt in this task's folder with exactly one line: Task Continuity is ready. Read it back and check that it matches. Use only file tools. Do not browse the web, run shell commands, or change files outside this task folder. Finish by reporting the saved file's full path.

When the task completes, open `welcome.txt` and check that it says `Task Continuity is ready.` It normally lives under `data/Tasks/<task-id>/`. Each task has its own folder, shown in the saved progress details.

Need help with any step? The [first-run guide](00-START-HERE.md) covers setup, expected results, and common errors. Once the file test works, try an [example task](docs/example-tasks.md).

## Staying in control

| Control | What it does |
| --- | --- |
| **Pause** | Stops reasoning. Existing jobs may keep running, and the deadline still counts down. |
| **Resume** | Continues saved work with a fresh budget from now. |
| **Stop** | Cancels the task and its owned jobs. Saved files stay on disk. |
| **Send follow-up** | Adds an instruction to the selected task. Active tasks keep their deadline; inactive tasks get a fresh budget. |

Time limits range from **0.1 to 72 hours**, including 50 hours. They are a maximum: checked work should finish early. Only one task controls the computer at a time. Closing the browser tab leaves the agent running; the normal launcher and Desktop shortcut use a watchdog to recover the supervisor.

The default settings select the first loaded model for a new task and keep that choice across restarts. Reload the same model if it becomes unavailable, or set `preferred_model` explicitly. [Configuration](docs/configuration.md) explains model selection, recovery settings, and other compatible local backends.

## Tools and optional setup

| Work | What you need |
| --- | --- |
| Text files and tracked PowerShell jobs | The installed Python runtime and a tool-capable model |
| Web search, downloads, and browser work | Installed Chrome by default; set `browser_channel` to `"msedge"` for Edge |
| Screenshots and desktop input | Windows signed in and unlocked; an image-capable model for visual review |
| Video encoding and review | ffmpeg and ffprobe on PATH |
| Blender projects and renders | Blender, `capabilities.blender: true`, and an image-capable model for visual review |
| Extra MCP integrations | Each server installed and configured separately |

Blender is disabled by default. Background Blender jobs do not need a bridge add-on; optional live control does. See [Blender setup](docs/blender.md).

Another local backend must support model discovery, streaming chat completions, and tool calls. An “OpenAI-compatible” label does not establish compatibility with every model or server. No cloud API key is bundled or required. Web tasks still contact external sites, and configured integrations have their own data handling.

## Saved work and recovery

Tasks normally live under `data/Tasks/<task-id>`. The agent keeps the brief, checkpoints, job receipts, and outputs there. If you name an external project, it can edit files in that project too; ask it to record those changed files as deliverables.

After an interruption, inspect the files and existing jobs before retrying: the last action may already have finished. Recovery uses saved state while the deadline is valid. Completed, paused, blocked, stopped, or expired tasks need **Resume** or a follow-up.

Older conversations are archived, file-tool backups are bounded, and logs rotate. Outputs, downloads, and context archives remain for recovery, so there is **no total disk quota**. Check free space and remove old tasks when you no longer need them. Direct and requested browser downloads have a per-file size limit; browser cancellation can briefly overshoot it. Shell commands and third-party programs do not inherit file-tool backups or download limits.

## Before leaving it running

Run the setup check from the project folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

Keep the computer powered. Active work requests wakefulness, but power loss and Windows shutdown still stop running processes. Desktop work also needs an unlocked session.

The agent uses your Windows account's permissions. It is not a security sandbox; use backups and recoverable files. Capability switches do not sandbox arbitrary shell commands. Login, purchases, publishing, external messages, and privilege changes require human authorization or action where applicable. See [security and privacy](SECURITY.md).

The release has **not** had a full overnight or 50-hour endurance test, or an extended artistic Blender refinement run. Structural checks cannot prove arbitrary code correct or an image artistically finished. Changing websites, add-on compatibility, GPU memory pressure, and login or CAPTCHA can still interrupt work. [Release status](RELEASE-STATUS.md) records the checks actually run and their limits.

## Help improve it

A short, reproducible report from another Windows machine or model backend is useful. Start with recoverable files, say what you expected and what happened, and remove private data from shared logs. The [benchmark guide](docs/benchmarking.md) and [roadmap](docs/roadmap.md) suggest concrete ways to help. Setup failures, uncertain recovery, and duplicate actions take priority over new integrations.

To validate and package the source:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

The build checks the source, dependencies, tests, settings, PowerShell syntax, and privacy rules. It produces a reproducible source ZIP, checksum, and file manifest in `dist/`. Task data, browser profiles, models, and `.venv` are excluded. Follow the [publishing guide](docs/publishing.md) when sharing a copy; manually uploading an installed folder can expose private files.

CI is configured for Windows with Python 3.11, 3.12, and 3.14. Check [the actual workflow results](https://github.com/orcaonmars/task-continuity-agent/actions) for the published commit. Node.js 18+ runs the optional interface checks; the app itself does not need Node. CI uses Node.js 22 and provides the verified Python 3.12 source package as a workflow artifact when that build passes.

Read the [architecture](docs/architecture.md) and [contribution guide](CONTRIBUTING.md) for development details.

## License

[MIT](LICENSE). Contributions that improve recovery, verification, and clarity are welcome.
