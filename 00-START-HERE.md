# Installation and usage

## Installation

Requirements:

- Windows 10 or 11.
- [Python 3.11 or later](https://www.python.org/downloads/windows/), available as `python` in PowerShell.
- A local model server with streaming responses and tool-call support. [LM Studio](https://lmstudio.ai/) is the default backend; models are installed separately.

Extract or clone the project into a writable folder. Open PowerShell in that folder. In File Explorer, you can type `powershell` in the folder's address bar and press Enter. Run the installer as your normal Windows user:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

The installer creates `.venv`, installs dependencies, validates the installation, writes local settings to `data/config.json`, and adds a **Task Continuity Agent** Desktop shortcut. It refuses administrator execution.

## Configuration

### Model server

In LM Studio, load a model trained for tool use. Open **Developer** and enable **Start server**. The app expects `http://127.0.0.1:1234/v1` by default. See [LM Studio's server documentation](https://lmstudio.ai/docs/developer/core/server).

Tool-call support is required for the model to perform actions through the app. Image inspection additionally requires a model and backend that accept images. Text-only models can use file, page-structure, accessibility, and background-job tools, but cannot perform visual review.

Edit `data/config.json` for a different endpoint or model selection. With `preferred_model` empty, a new task selects the first loaded model and retains that choice across restarts. Set `preferred_model` to select a specific model or explicitly replace a task's saved selection. Compatibility with other OpenAI-compatible servers depends on their streaming and tool-call behavior.

See [configuration reference](docs/configuration.md) for provider settings, automatic model recovery, timeouts, and optional tools. Blender is disabled by default; [Blender setup](docs/blender.md) covers background rendering and optional live control.

### Launch

Run from the project folder, or use the Desktop shortcut:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-agent.ps1
```

The launcher opens `http://127.0.0.1:8765/` by default and starts the watchdog. If that port is occupied, set an unused `ui_port` in `data/config.json` before launching. Closing the browser tab leaves the agent running.

## Usage

Select **New task**, enter the instructions, set **Time limit**, and select **Send task**. Include the source or destination folder, required deliverables, verification criteria, and any restrictions. Task budgets range from **0.1 to 72 hours**. The budget is a maximum; verified work can finish early.

For example, replace the path in this read-only project review:

> Review [full project folder path] for reliability defects. Use read-only source inspection; do not edit project files, run project code, install dependencies, or publish changes. Save findings.md in this task's folder with prioritized findings, file references, supporting evidence, and suggested fixes. Distinguish confirmed defects from possible risks and note any checks that require execution. Report the saved path when finished.

This produces a review without authorizing changes to the source project. Other [task examples](docs/example-tasks.md) cover implementation, research, data processing, and Blender.

### Task controls

| Control | Behavior |
| --- | --- |
| **Pause** | Suspends reasoning. Existing jobs may continue, and the deadline keeps running. |
| **Resume** | Continues saved work with a fresh budget using the selected hours. |
| **Stop** | Cancels the task and its owned jobs. Saved files remain. |
| **Send follow-up** | Adds instructions to the selected task. Active tasks retain their deadline; inactive tasks receive a fresh budget. |

Only one task controls the computer at a time.

### Outputs and recovery

Tasks normally save under `data/Tasks/<task-id>/`. Select a task in the sidebar, then open **Saved progress & files** for its folder path, checkpoints, and reported outputs. Completed files can appear as **Open** buttons. An explicitly selected external project can contain the edited files instead.

The original brief, checkpoints, job records, and outputs remain across model-session restarts. If the app stops, relaunch it normally. Eligible interrupted tasks recover while their deadline is valid; completed, paused, blocked, stopped, and expired tasks need **Resume** or a follow-up. Inspect existing files and jobs before retrying an interrupted action, since it may already have completed. Continue polling a running job; changed commands require a new job name.

### Operating limits

The agent runs with your Windows account's permissions and is not a security sandbox. Keep backups. Shell commands and third-party tools do not inherit file-tool backup guarantees. Capability switches do not restrict the account's underlying authority. Login, purchases, publishing, external messages, and privilege changes require your authorization or action where applicable.

Desktop interaction needs an unlocked Windows session. Keep the computer powered: recovery cannot preserve running processes through shutdown or power loss. File-tool backups and logs are bounded, but outputs, downloads, and context archives are retained without a total disk quota. Check free space and remove old tasks when no longer needed.

The interface binds to this computer by default. Local model hosting does not keep web requests or configured integrations offline. Runtime data is excluded from the source release; review [publishing guidance](docs/publishing.md) before sharing an installed copy. See [release status](RELEASE-STATUS.md) for tested recovery paths and limits; a full overnight or 50-hour endurance run has not been performed.

## Troubleshooting

Run the setup diagnostics from the project folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

| Problem | Resolution |
| --- | --- |
| `python` is missing or opens the Microsoft Store | Install Python 3.11 or later with the `python` command available. Reopen PowerShell and check `python --version` before rerunning the installer. |
| Administrator execution refused | Use a normal PowerShell window. |
| Local model unavailable | Check the server address and loaded model. For an existing task, reload its selected model or set `preferred_model` explicitly. |
| Model describes actions without executing them | Confirm that the model and server support tool calls. A text response alone does not establish tool compatibility. |
| Port 8765 occupied | Set `ui_port` to an unused port, such as `8766`, then relaunch using the address printed by the launcher. |
| Interface fails to open | Run `start-agent.ps1` in PowerShell to read the error; inspect `data/Logs/supervisor-error.log` for startup details. |
| Task reaches its time limit | Inspect the saved outputs, select an additional budget, and use **Resume** or send a follow-up. |

Missing optional tools do not prevent core file work. Include the diagnostic result and relevant error when reporting a problem, with private paths and task contents removed.
