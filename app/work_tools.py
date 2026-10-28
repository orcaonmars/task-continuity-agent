"""Small, literal tools for local models; no nested shell quoting for file edits."""

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
import psutil
from common import (
    DATA_ROOT,
    ROOT,
    PYTHON,
    NO_WINDOW,
    atomic_json,
    atomic_text,
    read_json,
    task_dir,
    settings,
    tail_bytes,
    find_blender,
    resolve_path,
    capability_enabled,
)


def writable(path):
    """Resolve a writable user path while protecting the installed agent and task identity."""
    p = resolve_path(path)
    root = ROOT.resolve()
    data_root = DATA_ROOT.resolve()
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")).resolve()

    if p.is_relative_to(windows):
        raise ValueError("Windows system files are outside agent authority.")
    if p.is_relative_to(root) and not p.is_relative_to(data_root):
        raise ValueError(
            "The installed agent source/runtime is read-only to task tools. "
            "Work in the task folder or another user project path."
        )
    if p.is_relative_to(data_root) and not p.is_relative_to(task_dir()):
        raise ValueError(
            "Shared runtime settings and other tasks are read-only to file tools."
        )
    if p.parent == task_dir() and p.name in {
        "acceptance.json",
        "user-brief.md",
        "task.json",
        "agent-state.json",
        "result.json",
        "objective.md",
        "AGENTS.md",
        "progress.json",
        "steering.json",
        "quality-review.json",
        "visual-observations.json",
        "video-review.json",
    }:
        raise ValueError(
            "The supervisor owns this task-state file; use task_checkpoint or finish_task."
        )
    if p.is_relative_to(task_dir() / "Jobs") or p.is_relative_to(
        task_dir() / "Backups"
    ):
        raise ValueError("Job ledgers and tool backups are read-only to file tools.")
    return p


def _backup_existing(path):
    """Keep a bounded set of recoverable pre-edit copies."""
    path = Path(path)
    if not path.is_file():
        return
    keep = int(settings().get("backups_per_file", 20))
    if keep <= 0:
        return
    import shutil

    key = hashlib.sha256(str(path).lower().encode("utf-8")).hexdigest()[:12]
    folder = task_dir() / "Backups"
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / f"{key}-{time.time_ns()}-{path.name}"
    shutil.copy2(path, backup)
    # The filename may contain glob metacharacters such as square brackets.
    # The hash already identifies the complete original path.
    # copy2 preserves the source mtime, which can repeat or move backwards.
    # Use the backup creation timestamp embedded in our own filename instead.
    prior = sorted(
        folder.glob(f"{key}-*"), key=lambda p: int(p.name.split("-", 2)[1])
    )
    for old in prior[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass


def read_text(path: str, offset: int = 0, limit: int = 5000) -> str:
    """Read bounded UTF-8 text. offset/limit are characters; use successive offsets for large files."""
    p = resolve_path(path)
    if p.suffix.lower() in (
        ".blend",
        ".blend1",
        ".png",
        ".jpg",
        ".jpeg",
        ".mp4",
        ".glb",
        ".fbx",
        ".zip",
        ".exe",
        ".dll",
        ".gguf",
    ):
        raise ValueError(
            "This is binary, not text. Use view_image, blender_project inspect, or asset_archive_inspect as appropriate."
        )
    limit = max(1, min(limit, 12000))
    offset = max(0, offset)
    # Bound memory even if the model accidentally selects a multi-GB log/asset.
    skipped = 0
    with p.open(encoding="utf-8-sig", errors="replace") as stream:
        while skipped < offset:
            part = stream.read(min(8192, offset - skipped))
            if not part:
                break
            skipped += len(part)
        content = stream.read(limit + 1)
    more = len(content) > limit
    return json.dumps(
        {
            "path": str(p),
            "characters": None if more else skipped + len(content),
            "bytes": p.stat().st_size,
            "offset": offset,
            "text": content[:limit],
            "more": more,
        },
        ensure_ascii=False,
    )


def write_text(path: str, text: str, append: bool = False) -> str:
    """Atomically write UTF-8 text and retain bounded pre-edit backups."""
    p = writable(path)
    if append and p.exists() and p.stat().st_size > 32 * 1024 * 1024:
        raise ValueError(
            "This file is too large for an in-memory text append. Use a tracked streaming job."
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    _backup_existing(p)
    existing = p.read_text(encoding="utf-8") if append and p.exists() else ""
    content = existing + text
    atomic_text(p, content)
    return json.dumps(
        {
            "path": str(p),
            "characters": len(content),
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
    )


def list_files(
    path: str, pattern: str = "*", recursive: bool = False, limit: int = 60
) -> str:
    """List a relevant folder/glob, up to 200 entries. Avoid whole-PC inventories."""
    p = resolve_path(path)
    limit = max(1, min(limit, 200))
    entries = p.rglob(pattern) if recursive else p.glob(pattern)
    out = []
    for x in entries:
        out.append(
            {
                "path": str(x),
                "directory": x.is_dir(),
                "bytes": x.stat().st_size if x.is_file() else None,
            }
        )
        if len(out) >= limit:
            break
    return json.dumps(out, ensure_ascii=False)


def live_process(info):
    try:
        process = psutil.Process(info["pid"])
        return (
            process
            if abs(process.create_time() - info["created"]) < 0.1
            and process.is_running()
            else None
        )
    except (psutil.Error, KeyError, TypeError, ValueError):
        return None


def owned_job_pids(task):
    """PID/create-time ledgers identify only this task's background jobs."""
    found = set()
    for folder in (Path(task) / "Jobs").glob("*"):
        if not folder.is_dir() or not (folder / "job.json").is_file():
            continue
        if (folder / "result.json").exists():
            continue
        for name in ("launcher.json", "runner.json", "process.json"):
            process = live_process(read_json(folder / name, {}))
            if process:
                found.add(process.pid)
                try:
                    found.update(p.pid for p in process.children(recursive=True))
                except psutil.Error:
                    pass
                # A Windows venv launcher owns the actual Python runner. Killing that
                # wrapper would kill the runner even if its own PID was preserved.
                try:
                    parent = process.parent()
                    if (
                        parent
                        and str(ROOT / "app/job_runner.py").lower()
                        in " ".join(parent.cmdline()).lower()
                    ):
                        found.add(parent.pid)
                except psutil.Error:
                    pass
    return found


def terminate_process_tree(process, grace_seconds=5):
    """Terminate one owned process tree and wait until it is actually gone."""
    try:
        descendants = process.children(recursive=True)
    except psutil.Error:
        descendants = []

    targets = [*reversed(descendants), process]
    for target in targets:
        try:
            target.terminate()
        except psutil.Error:
            pass

    _, alive = psutil.wait_procs(targets, timeout=max(0.1, grace_seconds))
    for target in alive:
        try:
            target.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=3)


def stop_task_jobs(task):
    for folder in (Path(task) / "Jobs").glob("*"):
        if not folder.is_dir() or not (folder / "job.json").is_file():
            continue
        if (folder / "result.json").exists():
            continue

        atomic_json(folder / "cancel.json", {"reason": "Stopped by user"})

        seen = set()
        for name in ("process.json", "runner.json", "launcher.json"):
            process = live_process(read_json(folder / name, {}))
            if process and process.pid not in seen:
                seen.add(process.pid)
                terminate_process_tree(process)

        atomic_json(
            folder / "result.json",
            {
                "exit_code": -1,
                "error": "Stopped by user",
                "finished_at": time.time(),
            },
        )


def job_status(folder):
    folder = Path(folder)
    result = read_json(folder / "result.json")
    runner = live_process(read_json(folder / "runner.json", {}))
    launcher = live_process(read_json(folder / "launcher.json", {}))
    child = live_process(read_json(folder / "process.json", {}))

    if not result and not runner and not launcher and not child:
        # The child can exit just before the runner publishes its result.
        time.sleep(0.1)
        result = read_json(folder / "result.json")

    status = (
        "finished"
        if result
        else "running"
        if runner or launcher or child
        else "interrupted"
    )
    logs = {}
    for name in ("stdout", "stderr", "runner-errors"):
        path = folder / f"{name}.log"
        if path.exists():
            logs[name] = tail_bytes(path, 4500).decode("utf-8", errors="replace")

    response = {
        "job_name": folder.name,
        "status": status,
        "result": result,
        "logs": logs,
        "job_folder": str(folder),
    }
    if status == "interrupted":
        response["recovery"] = (
            "The prior runner disappeared without a durable result. Inspect outputs before "
            "retrying. Reuse this job name only if no process was ever launched; otherwise "
            "use a new job name after deciding the action is safe to repeat."
        )
    return response


def _launch_job_runner(folder):
    """Record launch intent before spawning; uncertain launches are never replayed."""
    atomic_json(folder / "launch-intent.json", {"at": time.time()})
    try:
        with (folder / "runner-errors.log").open("ab") as runner_log:
            try:
                runner = subprocess.Popen(
                    [str(PYTHON), str(ROOT / "app" / "job_runner.py"), str(folder)],
                    # MCP's Windows kill-on-close job otherwise kills this
                    # independent runner when the reasoning actor disappears.
                    creationflags=NO_WINDOW | (0x01000000 if os.name == "nt" else 0),
                    start_new_session=os.name != "nt",
                    stdin=subprocess.DEVNULL,
                    stdout=runner_log,
                    stderr=runner_log,
                    close_fds=True,
                )
            except OSError as error:
                if os.name == "nt" and getattr(error, "winerror", None) == 5:
                    raise RuntimeError(
                        "Windows denied independent job launch. The MCP owner must allow "
                        "tracked runner breakaway; no coupled fallback was launched. "
                        "Launch intent was retained for inspection."
                    ) from error
                raise
        created = psutil.Process(runner.pid).create_time()
        atomic_json(
            folder / "launcher.json",
            {"pid": runner.pid, "created": created},
        )
        # Keep the process handle alive and reap it without blocking the actor.
        threading.Thread(target=runner.wait, daemon=True).start()
        return runner
    except Exception:
        # Spawn may already have succeeded. Keep the ledger and require inspection.
        raise


def start_job(name, argv, cwd=None, timeout=7200):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,70}", name):
        raise ValueError(
            "job_name must contain only letters/numbers/underscore/hyphen."
        )
    if not argv or not str(argv[0]).strip():
        raise ValueError("A job command is required.")
    argv = [str(value) for value in argv]
    working_directory = resolve_path(cwd) if cwd else task_dir().resolve()
    if not working_directory.is_dir():
        raise ValueError(f"Job working directory does not exist: {working_directory}")

    def input_path(arg):
        path = Path(arg)
        return path if path.is_absolute() else working_directory / path

    folder = task_dir() / "Jobs" / name
    deadline = read_json(task_dir() / "task.json", {}).get("deadline")
    if deadline and time.time() >= deadline:
        raise ValueError("Task deadline reached; no new job started.")

    for arg in argv:
        if str(arg).lower().endswith(".py") and input_path(arg).is_file():
            try:
                compile(
                    input_path(arg).read_text(encoding="utf-8-sig"),
                    str(arg),
                    "exec",
                )
            except SyntaxError as error:
                raise ValueError(
                    f"Script syntax error before launch: {error.msg} at line "
                    f"{error.lineno}. Fix the file and retry."
                ) from error

    inputs = {}
    for arg in argv:
        if str(arg).lower().endswith((".py", ".blend")):
            path = input_path(arg)
            if path.is_file():
                with path.open("rb") as stream:
                    inputs[str(path.resolve())] = hashlib.file_digest(
                        stream, "sha256"
                    ).hexdigest()

    existing = read_json(folder / "job.json")
    if existing is not None:
        if (
            existing.get("argv") != argv
            or ("input_files" in existing and existing.get("input_files") != inputs)
            or Path(existing.get("cwd", "")).resolve() != working_directory
        ):
            raise ValueError(
                "This job_name already refers to another command. Use a new name."
            )
        status = job_status(folder)
        return status

    if Path(argv[0]).name.lower() == "blender.exe" and any(
        value in argv for value in ("-b", "--background")
    ):
        for other in (task_dir() / "Jobs").glob("*"):
            spec = read_json(other / "job.json", {}) or {}
            other_args = spec.get("argv", [])
            if (
                other_args
                and Path(other_args[0]).name.lower() == "blender.exe"
                and job_status(other)["status"] == "running"
            ):
                raise ValueError(
                    f"Blender job {other.name} is already running. "
                    "Wait for it instead of starting a competing render."
                )

    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir()
    except FileExistsError:
        raise ValueError(
            "Job name is already claimed. Inspect it and use a new name after recovery."
        ) from None
    spec = {
        "version": 1,
        "argv": [str(value) for value in argv],
        "cwd": str(working_directory),
        "timeout_seconds": max(10, min(int(timeout), 259200)),
        "started_at": time.time(),
        "input_files": inputs,
    }
    atomic_json(folder / "job.json", spec)
    _launch_job_runner(folder)
    time.sleep(0.1)
    return job_status(folder)


def write_job_script(path, code):
    """Create immutable helper code for one job name."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != code:
            raise ValueError(
                "This helper/job name already has different settings. "
                "Use a NEW job_name; the existing helper was preserved."
            )
        return
    atomic_text(path, code)


def shell(command: str, job_name: str, timeout_seconds: int = 3600) -> str:
    """Start a normal-user PowerShell command as a tracked background job. Returns immediately.
    Use jobs(status) to read logs/exit code; do not relaunch while running. Same job_name is idempotent.
    New retry needs a new name. timeout_seconds is capped at 259200 (72 hours) and the task deadline.
    Stop for admin/UAC/login/CAPTCHA. Use write_text for multiline code."""
    # Deliberate safety tripwires, not a security sandbox. Normal user access remains broad.
    if re.search(
        r"(?i)\b(format-volume|format-disk|diskpart|bcdedit|disable-antimalware)\b"
        r"|\b-verb\s+runas\b",
        command,
    ):
        raise ValueError("This operation is outside normal-user agent authority.")

    root_text = str(ROOT).lower().replace("/", "\\")
    normalized_command = command.lower().replace("/", "\\")
    destructive = re.search(
        r"(?i)\b(remove-item|del|erase|rmdir|rd|move-item|rename-item|"
        r"set-content|out-file|clear-content|copy-item)\b",
        command,
    )
    if root_text in normalized_command and destructive:
        raise ValueError(
            "Task shell commands may not modify the installed agent source/runtime. "
            "Use the task folder or another user project path."
        )
    # The supervisor injects authoritative remaining_minutes/deadline into every model turn.
    # Historical long runs wasted dozens of shell calls re-reading the system clock. Block
    # only jobs that are clearly named as time/deadline probes AND contain clock primitives;
    # normal benchmark/render scripts that measure elapsed time remain allowed.
    probe_name = bool(
        re.search(
            r"(?i)(?:^|[_-])(clock|deadline|timecheck|time_check|check_time|tc_now|state_check_now)(?:$|[_-])",
            job_name,
        )
    )
    probe_cmd = bool(
        re.search(
            r"(?i)(get-date|toUnixTimeSeconds|uformat\s+%s|time\.time\s*\(|datetime\.(?:now|utcnow)|remaining(?:sec|_sec|_min|minutes)|deadline)",
            command,
        )
    )
    if probe_name and probe_cmd:
        raise ValueError(
            "Do not shell-check the clock or task deadline. CURRENT FACTS already contains authoritative remaining_minutes and deadline_authoritative; use those and continue productive work."
        )
    import shutil

    ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
    return json.dumps(
        start_job(
            job_name,
            [ps, "-NoProfile", "-NonInteractive", "-Command", command],
            timeout=timeout_seconds,
        ),
        ensure_ascii=False,
    )


def jobs(action: str = "list", job_name: str = "") -> str:
    """List, inspect, or stop tracked jobs for the current task."""
    root = task_dir() / "Jobs"
    if action == "list":
        entries = []
        for folder in root.iterdir() if root.exists() else []:
            if not folder.is_dir():
                continue
            info = job_status(folder)
            entries.append(
                {
                    "job_name": folder.name,
                    "status": info["status"],
                    "exit_code": (info.get("result") or {}).get("exit_code"),
                    "started_at": read_json(folder / "job.json", {}).get(
                        "started_at", 0
                    ),
                }
            )
        entries.sort(
            key=lambda item: (item["status"] == "running", item["started_at"]),
            reverse=True,
        )
        return json.dumps(
            {
                "jobs": entries[:30],
                "total": len(entries),
                "hint": (
                    "Recent/running jobs first. Use status for logs. "
                    "Do not relaunch a running or uncertain interrupted job."
                ),
            }
        )

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,70}", job_name):
        raise ValueError("Invalid job name.")
    folder = root / job_name
    if not (folder / "job.json").exists():
        raise ValueError("No such job.")

    if action == "stop" and not read_json(folder / "result.json"):
        atomic_json(folder / "cancel.json", {"reason": "Stopped"})
        seen = set()
        for filename in ("process.json", "runner.json", "launcher.json"):
            process = live_process(read_json(folder / filename, {}))
            if process and process.pid not in seen:
                seen.add(process.pid)
                terminate_process_tree(process)
        atomic_json(
            folder / "result.json",
            {
                "exit_code": -1,
                "error": "Stopped",
                "finished_at": time.time(),
            },
        )
    elif action not in ("status", "stop"):
        raise ValueError("Use list, status or stop.")

    return json.dumps(job_status(folder), ensure_ascii=False)


def blender_project(
    action: str,
    job_name: str,
    project: str = "",
    script: str = "",
    output: str = "",
    subject_names: list[str] | None = None,
    frame: int = 1,
    width: int = 960,
    height: int = 540,
    camera_padding: float = 1.4,
    asset_path: str = "",
) -> str:
    """Isolated Blender: import_asset, script, inspect, api, fit_camera, render_preview.
    script is an existing .py path or complete inline Python; explicitly save/render in code.
    inspect summarizes geometry/materials; subject_names filters objects. api gives installed
    RNA enums for subject_names type names (e.g. ShaderNodeTexSky), no project needed.
    import_asset takes asset_path (.obj/.glb/.gltf/.fbx/.blend) and a NEW output .blend.
    It imports actual model geometry/materials and saves; use jobs(status) before the next step.
    Existing project stays untouched unless output names a new .blend. fit_camera frames subject
    geometry with padding; exclude ground/landscape. It saves a NEW project. Use jobs to await results.
    Use the configured live Blender bridge for interactive scene work. No old checkpoints are chosen automatically."""
    if not capability_enabled("blender"):
        raise RuntimeError("Blender capability is disabled in config.json.")
    exe = find_blender()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,70}", job_name):
        raise ValueError("Invalid job_name.")
    args = [
        exe,
        "-b",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
    ]
    if project:
        source = resolve_path(project)
        if not source.is_file() or source.suffix.lower() != ".blend":
            raise ValueError("Existing .blend needed.")
        args.append(str(source))
    if action == "script":
        if "\n" in script or script.lstrip().startswith(("import ", "from ")):
            compile(script, "<inline Blender script>", "exec")
            inline = task_dir() / "Scripts" / ("inline-" + job_name + ".py")
            write_job_script(inline, script)
            script = str(inline)
        source = resolve_path(script)
        if not source.is_file() or source.suffix != ".py":
            raise ValueError("Write a Python script first.")
        args += ["--python", str(source)]
    else:
        code = (
            "import bpy,json,math\nfrom mathutils import Vector\ns=bpy.context.scene\ns.frame_set("
            + str(int(frame))
            + ")\n"
        )
        if action == "import_asset":
            asset = resolve_path(asset_path)
            if not asset.is_file() or asset.suffix.lower() not in (
                ".obj",
                ".glb",
                ".gltf",
                ".fbx",
                ".blend",
            ):
                raise ValueError(
                    "Select an existing OBJ, GLB/glTF, FBX or Blender asset."
                )
            destination = writable(output)
            if (
                destination.suffix.lower() != ".blend"
                or destination == asset
                or destination.exists()
            ):
                raise ValueError(
                    "Import into a NEW .blend path; the source asset stays unchanged."
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            code += "asset=" + repr(str(asset)) + "\n"
            if not project:
                code += 'bpy.ops.object.select_all(action="SELECT");bpy.ops.object.delete(use_global=False)\n'
            if asset.suffix.lower() == ".obj":
                code += "bpy.ops.wm.obj_import(filepath=asset)\n"
            elif asset.suffix.lower() in (".glb", ".gltf"):
                code += "bpy.ops.import_scene.gltf(filepath=asset)\n"
            elif asset.suffix.lower() == ".fbx":
                code += "bpy.ops.import_scene.fbx(filepath=asset)\n"
            else:
                code += "wanted=" + repr(subject_names or []) + "\n"
                code += "with bpy.data.libraries.load(asset,link=False) as (available,loaded):\n    loaded.objects=[name for name in available.objects if not wanted or name in wanted]\nfor obj in loaded.objects:\n    if obj is not None and obj.name not in s.objects:s.collection.objects.link(obj)\n"
            code += 'bpy.context.view_layer.update()\nmeshes=[o for o in s.objects if o.type=="MESH"]\nif not meshes:raise RuntimeError("Import produced no mesh objects.")\n'
            code += (
                "bpy.ops.wm.save_as_mainfile(filepath="
                + repr(str(destination))
                + ",compress=False)\n"
            )
            code += 'print("ASSET_IMPORTED:"+json.dumps({"source":asset,"meshes":len(meshes),"vertices":sum(len(o.data.vertices) for o in meshes),"materials":len(bpy.data.materials),"output":bpy.data.filepath}))\n'
        elif action in ("inspect", "api"):
            code += (
                "SUBJECT_NAMES="
                + repr(subject_names or [])
                + "\nPROBE_MODE="
                + repr(action)
                + "\n"
            )
            code += (ROOT / "app/blender_probe.py").read_text(encoding="utf-8")
        elif action == "fit_camera":
            destination = writable(output)
            if destination.suffix.lower() != ".blend" or destination.exists():
                raise ValueError("Output must be a new .blend.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if project and resolve_path(output) == resolve_path(project):
                raise ValueError("Save the fitted camera to a NEW project path.")
            code += "names=" + repr(subject_names or []) + "\n"
            code += (
                """subjects=[o for o in s.objects if o.type=='MESH' and (o.name in names if names else not any(w in o.name.lower() for w in ('ground','road','terrain','plane')))]
if not subjects: raise ValueError('Specify subject_names; no subject meshes found.')
points=[o.matrix_world @ Vector(c) for o in subjects for c in o.bound_box]
center=sum(points,Vector())/len(points)
camera=s.camera
if camera is None:
    camera=bpy.data.objects.new('Camera',bpy.data.cameras.new('Camera'));s.collection.objects.link(camera);s.camera=camera
camera.data.type='PERSP';camera.data.lens=45
direction=(camera.location-center).normalized() if (camera.location-center).length>.1 else Vector((1,-1,.65)).normalized()
camera.location=center+direction*10
camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
s.render.resolution_x=WIDTH;s.render.resolution_y=HEIGHT;s.render.resolution_percentage=100
bpy.context.view_layer.update()
location,scale=camera.camera_fit_coords(bpy.context.evaluated_depsgraph_get(),[v for p in points for v in p])
camera.location=center+(location-center)*PADDING
camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
from bpy_extras.object_utils import world_to_camera_view
ndc=[world_to_camera_view(s,camera,p) for p in points]
for attempt in range(12):
    if all(.06<p.x<.94 and .06<p.y<.94 and p.z>0 for p in ndc):break
    camera.location=center+(camera.location-center)*1.1;bpy.context.view_layer.update()
    ndc=[world_to_camera_view(s,camera,p) for p in points]
else:raise ValueError('Subject framing check failed.')
print('FRAMING_METRICS:'+json.dumps({'x_range':[min(p.x for p in ndc),max(p.x for p in ndc)],'y_range':[min(p.y for p in ndc),max(p.y for p in ndc)],'subject_names':[o.name for o in subjects]}))
""".replace("WIDTH", str(max(64, min(int(width), 3840))))
                .replace("HEIGHT", str(max(64, min(int(height), 2160))))
                .replace("PADDING", str(max(1.1, min(float(camera_padding), 3))))
            )
            code += (
                "bpy.ops.wm.save_as_mainfile(filepath="
                + repr(str(resolve_path(output)))
                + ",compress=False)\n"
            )
            code += "print('FRAMING_VERIFIED: all subject bounding-box corners visible with margin')\n"
        elif action == "render_preview":
            writable(output).parent.mkdir(parents=True, exist_ok=True)
            code += (
                "s.render.resolution_x="
                + str(max(64, min(width, 1920)))
                + "\ns.render.resolution_y="
                + str(max(64, min(height, 1080)))
                + "\ns.render.resolution_percentage=100\n"
            )
            code += (
                's.render.image_settings.file_format="PNG"\ns.render.filepath='
                + repr(str(resolve_path(output)))
                + "\nbpy.ops.render.render(write_still=True)\n"
            )
            code += "import hashlib\nfrom pathlib import Path\nfrom bpy_extras.object_utils import world_to_camera_view\nbounds=[]\nfor o in s.objects:\n    if o.type!='MESH' or any(w in o.name.lower() for w in ('ground','road','terrain','plane')):continue\n    p=[world_to_camera_view(s,s.camera,o.matrix_world@Vector(c)) for c in o.bound_box]\n    bounds.append({'object':o.name,'x':[min(v.x for v in p),max(v.x for v in p)],'y':[min(v.y for v in p),max(v.y for v in p)],'inside_frame':all(0<=v.x<=1 and 0<=v.y<=1 and v.z>0 for v in p)})\n"
            code += (
                "project_path="
                + repr(str(resolve_path(project)) if project else "")
                + "\n"
            )
            code += "digest=None\nif project_path:\n    with open(project_path,'rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()\ninfo={'project':project_path,'project_sha256':digest,'frame':s.frame_current,'resolution':[s.render.resolution_x,s.render.resolution_y],'bounds':bounds[:40],'note':'Bounds describe frame-edge clipping. They do not determine occlusion or artistic quality.'}\n"
            code += (
                "Path("
                + repr(str(resolve_path(output)) + ".render.json")
                + ').write_text(json.dumps(info),encoding="utf-8")\nprint("RENDER_INFO:"+json.dumps(info))\n'
            )
        else:
            raise ValueError("Use script, inspect, fit_camera or render_preview.")
        path = task_dir() / "Scripts" / ("helper-" + job_name + ".py")
        path.parent.mkdir(exist_ok=True)
        write_job_script(path, code)
        args += ["--python", str(path)]
    return json.dumps(start_job(job_name, args, timeout=7200), ensure_ascii=False)
