# Your first task

Start with a small text file. It checks that the app and your model can work together before you give them a bigger job.

## 1. Get ready

You need Windows 10 or 11, [Python 3.11 or later](https://www.python.org/downloads/windows/), and a local model that supports **tool use**. That means the model can ask the app to read or write a file, rather than only describe what to do. A model and [LM Studio](https://lmstudio.ai/) are separate downloads; neither is included here.

Extract the project ZIP into a folder you can write to, such as Documents. Open that folder in File Explorer, type `powershell` in the address bar, and press Enter. Use an ordinary PowerShell window, not “Run as administrator.”

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

The installer downloads the Python dependencies, checks the installation, creates your local settings at `data/config.json`, and adds a **Task Continuity Agent** Desktop shortcut. Wait for “installed and validated” before continuing. If it stops with an error, use the troubleshooting steps below.

## 2. Connect your model

In LM Studio, load a model that supports tool use. Open **Developer** and switch on **Start server**. The default address expected by this app is `http://127.0.0.1:1234/v1`. [LM Studio's server guide](https://lmstudio.ai/docs/developer/core/server) shows where to find these controls.

For your first run, load just the model you want to use. With the default settings, a new task selects the first loaded model and remembers that choice. Other local servers need [additional configuration](docs/configuration.md) and may differ in compatibility.

Text-only models can do this first task. Visual tasks, such as judging a rendered image, need a model and server that accept image inputs. Blender and extra integrations can wait until the basics work.

## 3. Open the app

In the same PowerShell window, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-agent.ps1
```

You can use the Desktop shortcut on later runs. The launcher opens the app in your browser, normally at `http://127.0.0.1:8765/`. This address belongs to your computer. Closing the browser tab leaves the agent running.

## 4. Make one file

Select **New task**, set **Time limit** to **0.1 hours** (a maximum of six minutes), and paste this:

> Create welcome.txt in this task's folder with exactly one line: Task Continuity is ready. Read it back and check that it matches. Use only file tools. Do not browse the web, run shell commands, or change files outside this task folder. Finish by reporting the saved file's full path.

Select **Send task**. A successful run ends with **Completed** and a reported `welcome.txt` file. Select **Open welcome.txt**, then check its contents:

```text
Task Continuity is ready.
```

The file normally lives at `data/Tasks/<task-id>/welcome.txt` inside this installation. Each task gets its own folder; its full path appears in the saved progress details. If you do not see an output button, use the path reported in the conversation or saved progress.

You now have a useful starting point. Try an [example task](docs/example-tasks.md) with a clear result and a short budget before leaving anything to run overnight.

## While a task is running

| Control | What happens |
| --- | --- |
| **Pause** | Stops the agent's reasoning. Jobs it already started may keep running, and the deadline keeps counting down. |
| **Resume** | Continues from saved work with a fresh budget, using the hours you have selected. |
| **Stop** | Cancels the task and its owned jobs. Saved files remain. |
| **Send follow-up** | Adds an instruction to the selected task. An active task keeps its deadline; an inactive task gets a fresh budget. |

A budget is a limit, not a target. The agent should finish early when the result is checked. Only one task controls the computer at a time.

## If something gets stuck

| What you see | What to try |
| --- | --- |
| `python` is missing or opens the Microsoft Store | Install Python 3.11 or later with the `python` command available. Reopen PowerShell, check `python --version`, then rerun the installer. |
| “Run normally, not as Administrator” | Close that PowerShell window and open a normal one from the project folder. |
| Local model unavailable | Check that the local server is running at the expected address and a tool-capable model is loaded. If the task already selected a model, reload that same model. |
| Model replies with instructions but creates no file | Check that the model supports tool calls through your server. Try the first task in a new task; a text reply alone is not a successful file test. |
| Port 8765 is occupied | Before launching, change `ui_port` in `data/config.json` to an unused port, such as `8766`. Rerun the launcher and use the address it prints. |
| The app does not open | Run `start-agent.ps1` from PowerShell so you can read the error. Startup details are in `data/Logs/supervisor-error.log`. |

For a setup report, run this from the project folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

The doctor checks the runtime, local server, settings, and optional tools. Missing optional tools do not prevent the text-file task. Before asking for help, remove private paths and task contents from any logs you share.

## Before bigger work

Keep backups and use files you can recover. The agent runs with your Windows account's permissions; it is not a security sandbox. Desktop interaction needs Windows signed in and unlocked. Keep the computer powered, and check free space: saved tasks, downloads, and outputs remain on disk.

Recovery has been tested with interrupted tasks, but this release has not had a full overnight or 50-hour endurance test. Read [current limits and test evidence](RELEASE-STATUS.md) before a long run.

Your task data and local environment are excluded from the source release. If you publish a copy, follow [the publishing guide](docs/publishing.md); do not manually upload the whole installed folder. Git ignore rules do not protect a manual web upload.
