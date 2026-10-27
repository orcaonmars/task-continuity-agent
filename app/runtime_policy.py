"""Small-context tool routing and independent progress accounting."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from common import read_json
from work_tools import job_status

CORE = {
    "read_text",
    "write_text",
    "list_files",
    "shell",
    "jobs",
    "task_brief",
    "task_checkpoint",
    "finish_task",
}

GROUPS = {
    "web": {
        "web_search",
        "sketchfab_search",
        "asset_archive_inspect",
        "download_file",
        "browser",
        "view_image",
    },
    "desktop": {"desktop", "view_image"},
    "media": {
        "view_image",
        "contact_sheet",
        "video_review_frames",
        "quality_review",
        "encode_video",
    },
    "blender": {
        "blender_project",
        "blender",
        "ensure_blender",
        "blender_asset_audit",
        "view_image",
        "render_sequence",
        "encode_video",
        "contact_sheet",
        "video_review_frames",
        "quality_review",
    },
}

SELECT = {
    "type": "function",
    "function": {
        "name": "use_tools",
        "description": (
            "Switch the optional capability group for the next actions. "
            "Core file/shell/task tools stay available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "enum": ["core", "web", "desktop", "media", "blender", "extra"],
                }
            },
            "required": ["group"],
            "additionalProperties": False,
        },
    },
}

# Supervisor/internal folders and dependency/cache trees should never count as the
# user's durable production output. Pruning these also prevents a node_modules or
# virtualenv tree from turning every progress check into a filesystem benchmark.
IGNORED_DIRS = {
    "jobs",
    "backups",
    "scripts",
    "agentimages",
    "contextarchive",
    "videoreview",
    "screenshots",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    ".idea",
    ".vscode",
    "dist",
    "build",
}

META = {
    "user-brief.md",
    "objective.md",
    "agents.md",
    "acceptance.json",
    "task.json",
    "agent-state.json",
    "progress.json",
    "steering.json",
    "result.json",
    "quality-review.json",
    "visual-observations.json",
    "video-review.json",
    "events.jsonl",
    "raw-log.jsonl",
}

IGNORED_SUFFIXES = {
    ".tmp",
    ".log",
    ".jsonl",
    ".writing",
    ".pyc",
    ".pyo",
}

VISUAL = {
    ".blend",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".exr",
    ".mp4",
    ".mov",
    ".mkv",
    ".glb",
    ".gltf",
    ".obj",
    ".fbx",
}

SNAPSHOT_FILE_LIMIT = 20_000
SNAPSHOT_ENTRY_LIMIT = 50_000


READ_ONLY_TOOLS = {
    "read_text",
    "list_files",
    "task_brief",
    "view_image",
    "web_search",
    "sketchfab_search",
    "asset_archive_inspect",
    "task_checkpoint",
    "quality_review",
    "video_review_frames",
}

READ_ONLY_ACTIONS = {
    "browser": {"snapshot", "tabs", "screenshot"},
    "desktop": {"windows", "screenshot", "focus"},
    "blender_project": {"inspect", "api"},
}


def _receipt_progress_marker(state):
    """Newest successful state-changing receipt for tasks without file outputs."""
    newest = 0.0
    for receipt in state.get("receipts", []):
        if not receipt.get("ok"):
            continue
        tool = receipt.get("tool")
        if tool in READ_ONLY_TOOLS:
            continue
        if tool == "jobs":
            if receipt.get("exit_code") != 0:
                continue
        action = str(receipt.get("action") or "")
        if action and action in READ_ONLY_ACTIONS.get(tool, set()):
            continue
        newest = max(newest, float(receipt.get("at") or 0.0))
    return newest


def artifact_snapshot(folder, contract):
    """Return a bounded deterministic fingerprint of real task outputs.

    This intentionally uses metadata rather than hashing every file on every model step.
    Final completion verification performs stronger checks where appropriate.
    """
    folder = Path(folder)
    required = {str(ext).lower() for ext in contract.get("required_extensions", [])}
    visual = bool(contract.get("visual_review_required")) or bool(required & VISUAL)

    if not contract.get("requires_action", bool(required) or visual):
        return []

    rows = []
    seen_files = 0
    seen_entries = 0
    truncated = False

    # Bound work inspected, including unrelated files and empty directories.
    # os.walk materializes a whole directory before our output limit can apply.
    pending = [folder]
    while pending and not truncated:
        base = pending.pop()
        files = []
        directories = []
        try:
            with os.scandir(base) as entries:
                for entry in entries:
                    if seen_entries >= SNAPSHOT_ENTRY_LIMIT:
                        truncated = True
                        break
                    seen_entries += 1
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() not in IGNORED_DIRS:
                                # Windows junctions can leave the task tree or cycle.
                                if not getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                                    directories.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            files.append(entry.name)
                    except OSError:
                        continue
        except OSError:
            continue
        pending.extend(sorted(directories, reverse=True))

        for name in sorted(files):
            lower = name.lower()
            if lower in META or name.startswith("."):
                continue
            path = base / name
            suffix = path.suffix.lower()
            if suffix in IGNORED_SUFFIXES:
                continue
            if visual and suffix not in VISUAL:
                continue
            if required and not visual and suffix and suffix not in required:
                # If the prompt names concrete output extensions, use them as the
                # strongest generic progress signal and ignore unrelated repo churn.
                continue

            try:
                stat = path.stat()
                if not path.is_file():
                    continue
                relative = path.relative_to(folder)
            except (OSError, ValueError):
                continue

            rows.append((str(relative), stat.st_size, stat.st_mtime_ns))
            seen_files += 1
            if seen_files >= SNAPSHOT_FILE_LIMIT:
                truncated = True
                break

        if truncated:
            break

    # Watch actual files in an explicitly checkpointed external user project too.
    checkpoint = read_json(folder / "progress.json", {}) or {}
    if checkpoint.get("objective_id") == contract.get("objective_id") and contract.get(
        "objective_id"
    ):
        for value in checkpoint.get("deliverables", [])[:50]:
            try:
                path = Path(value).resolve()
                if path.is_relative_to(folder.resolve()) or not path.is_file():
                    continue
                if path.suffix.lower() in IGNORED_SUFFIXES or path.name.lower() in META:
                    continue
                if visual and path.suffix.lower() not in VISUAL:
                    continue
                if required and not visual and path.suffix.lower() not in required:
                    continue
                stat = path.stat()
                rows.append(("@external:" + str(path), stat.st_size, stat.st_mtime_ns))
            except (OSError, TypeError, ValueError):
                continue
    rows = sorted(set(rows))
    if truncated:
        # A stable sentinel makes the bounded behavior explicit in saved state.
        rows.append(("__snapshot_truncated__", SNAPSHOT_FILE_LIMIT, SNAPSHOT_ENTRY_LIMIT))
    return rows


def recent_jobs(folder, limit=5):
    rows = []
    for path in (Path(folder) / "Jobs").glob("*"):
        if not path.is_dir() or not (path / "job.json").is_file():
            continue
        spec = read_json(path / "job.json", {}) or {}
        info = job_status(path)
        result = info.get("result") or {}
        rows.append(
            {
                "job_name": path.name,
                "status": info["status"],
                "exit_code": result.get("exit_code"),
                "started_at": spec.get("started_at", 0),
            }
        )
    return sorted(
        rows,
        key=lambda item: (item["status"] == "running", item["started_at"]),
        reverse=True,
    )[:limit]


def record_receipt(state, name, arguments, text, error):
    receipt = {"tool": name, "ok": not error, "at": time.time()}
    for key in (
        "job_name",
        "path",
        "output",
        "project",
        "action",
        "section",
        "destination",
    ):
        if key in arguments:
            receipt[key] = str(arguments[key])[:240]

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if "job_name" in data:
                receipt.update(
                    job_name=data["job_name"],
                    status=data.get("status"),
                )
            if data.get("result"):
                receipt["exit_code"] = data["result"].get("exit_code")
            if "path" in data and name == "write_text":
                receipt["path"] = data["path"]
    except (ValueError, TypeError):
        pass

    if error:
        receipt["error"] = text[-400:]

    key = (
        receipt.get("tool"),
        receipt.get("job_name"),
        receipt.get("path"),
        receipt.get("section"),
        receipt.get("action"),
    )
    old = [
        item
        for item in state.get("receipts", [])
        if (
            item.get("tool"),
            item.get("job_name"),
            item.get("path"),
            item.get("section"),
            item.get("action"),
        )
        != key
    ]
    state["receipts"] = (old + [receipt])[-12:]


def progress_tick(state, folder, contract, live_job=False, now=None):
    now = time.time() if now is None else now
    # Persist the comparison representation too; JSON restores tuples as lists.
    current = [list(row) for row in artifact_snapshot(folder, contract)]
    file_changed = state.get("artifact_snapshot") is None or current != state.get(
        "artifact_snapshot"
    )
    state["artifact_snapshot"] = current

    durable_required = contract.get("durable_deliverable_required")
    if durable_required is None:
        durable_required = bool(
            contract.get("required_extensions")
            or contract.get("visual_review_required")
        )

    receipt_changed = False
    if contract.get("requires_action") and not durable_required:
        marker = _receipt_progress_marker(state)
        previous_marker = float(state.get("last_receipt_progress") or 0.0)
        receipt_changed = marker > previous_marker
        state["last_receipt_progress"] = max(marker, previous_marker)

    changed = file_changed or receipt_changed
    if changed:
        state["stagnant_steps"] = 0
        state["last_artifact_change"] = now
        state["last_progress_at"] = now
        state["progress_window_started"] = now
    elif not live_job:
        state["stagnant_steps"] = int(state.get("stagnant_steps", 0)) + 1
    if live_job:
        # A legitimate multi-hour build/render must not consume the model's
        # no-progress window before it can inspect the finished job's result.
        state["progress_window_started"] = now

    return changed, int(state.get("stagnant_steps", 0))
