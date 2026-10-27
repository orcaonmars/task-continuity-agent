"""Loopback UI and persistent supervisor for long-running local-model tasks."""

import ctypes
import hashlib
import json
import math
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import psutil
import requests
from common import (
    ROOT,
    DATA_ROOT,
    PYTHON,
    NO_WINDOW,
    append_text_line,
    atomic_json,
    archive_json,
    read_json,
    settings,
    standard_user,
    tail_bytes,
    ensure_data_layout,
    _tracked_blender_logger,
)
from acceptance import initialize_contract, validate_completion
from runtime_policy import artifact_snapshot
from work_tools import owned_job_pids, stop_task_jobs
from recovery import repair_stalled_state

PORT = int(settings().get("ui_port", 8765))
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
ACTIVE = None
PROC = None
STOP = threading.Event()
WAKE = threading.Event()
WAKE.set()
STEER = threading.Event()


def log(task, role, text):
    event = {"time": time.time(), "role": role, "text": str(text)}
    limit = int(settings().get("event_log_max_mb", 20)) * 1024 * 1024
    with LOCK:
        append_text_line(
            task / "events.jsonl",
            json.dumps(event, ensure_ascii=False),
            max_bytes=limit,
        )


def save_meta(task, **changes):
    with LOCK:
        meta = read_json(task / "task.json", {})
        meta.update(changes)
        if changes.get("status") in ("completed", "blocked", "stopped", "time_limit"):
            meta["finished_at"] = time.time()
        elif changes.get("status") in ("queued", "running"):
            meta.pop("finished_at", None)
        atomic_json(task / "task.json", meta)
    return meta


def keep_awake(active):
    # Temporary per-thread power request. Does not change any Windows power-plan settings.
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000003 if active else 0x80000000)


def model_info():
    """Return the currently selected local model and the key needed to reload it."""
    cfg = settings()
    base = cfg["api_base"].rstrip("/")
    preferred = str(cfg.get("preferred_model") or "").lower()
    provider = cfg["provider"]
    pinned = {}
    if not preferred and ACTIVE:
        pinned = read_json(ACTIVE / "task.json", {}) or {}
    pinned_id = str(pinned.get("model") or "").lower()
    pinned_key = str(pinned.get("model_key") or "").lower()

    if provider == "lmstudio":
        admin = base[:-3] if base.endswith("/v1") else base
        response = requests.get(admin + "/api/v1/models", timeout=5)
        response.raise_for_status()
        models = response.json().get("models", [])
        loaded = [
            model
            for model in models
            if model.get("loaded_instances") and model.get("type") == "llm"
        ]

        chosen = None
        if not preferred and (pinned_id or pinned_key):
            chosen = next(
                (
                    model
                    for model in loaded
                    if any(
                        str(instance.get("id", "")).lower() == pinned_id
                        for instance in model.get("loaded_instances", [])
                    )
                ),
                None,
            )
            chosen = chosen or next(
                (
                    model
                    for model in loaded
                    if pinned_key and str(model.get("key", "")).lower() == pinned_key
                ),
                None,
            )
            if not chosen:
                raise RuntimeError(
                    "The task's selected model is not loaded; reload it or set preferred_model explicitly."
                )
        if preferred:
            chosen = next(
                (
                    model
                    for model in loaded
                    if str(model.get("key", "")).lower() == preferred
                    or any(
                        str(instance.get("id", "")).lower() == preferred
                        for instance in model.get("loaded_instances", [])
                    )
                ),
                None,
            )
            if not chosen:
                chosen = next(
                    (
                        model
                        for model in loaded
                        if preferred in str(model.get("key", "")).lower()
                    ),
                    None,
                )
        if preferred and not chosen:
            raise RuntimeError(
                "The configured preferred_model is not loaded; load it or update config.json."
            )
        chosen = chosen or (loaded[0] if loaded else None)
        if not chosen:
            raise RuntimeError(
                "The local API is reachable but no LLM is loaded in LM Studio."
            )

        instance = next(
            (
                item
                for item in chosen["loaded_instances"]
                if str(item.get("id", "")).lower() == (preferred or pinned_id)
            ),
            chosen["loaded_instances"][0],
        )
        return {
            "model": instance["id"],
            "model_key": chosen.get("key") or instance["id"],
            "context": int(
                instance.get("config", {}).get(
                    "context_length", cfg.get("context_length", 16384)
                )
            ),
            "vision": bool(chosen.get("capabilities", {}).get("vision", False)),
            "reasoning_options": (
                chosen.get("capabilities", {}).get("reasoning") or {}
            ).get("allowed_options", []),
            "tools": bool(
                chosen.get("capabilities", {}).get("trained_for_tool_use", False)
            ),
            "provider": "lmstudio",
        }

    response = requests.get(base + "/models", timeout=5)
    response.raise_for_status()
    models = response.json().get("data", [])
    selection = preferred or pinned_id
    chosen = next(
        (model for model in models if str(model.get("id", "")).lower() == selection),
        None,
    )
    if not chosen and preferred:
        chosen = next(
            (
                model
                for model in models
                if preferred in str(model.get("id", "")).lower()
            ),
            None,
        )
    if preferred and not chosen:
        raise RuntimeError(
            "The configured preferred_model is not available; load it or update config.json."
        )
    if pinned_id and not preferred and not chosen:
        raise RuntimeError(
            "The task's selected model is not available; reload it or set preferred_model explicitly."
        )
    chosen = chosen or (models[0] if models else None)
    if not chosen:
        raise RuntimeError(
            "The configured OpenAI-compatible endpoint returned no models."
        )
    return {
        "model": chosen["id"],
        "model_key": chosen["id"],
        "context": int(cfg.get("context_length", 16384)),
        "vision": bool(cfg.get("vision", False)),
        "tools": True,
        "provider": provider,
    }


def child_environment(task):
    cfg = settings()
    env = dict(os.environ)
    env["TASK_CONTINUITY_TASK_DIR"] = str(task)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["RUST_LOG"] = "error"

    additions = [str(PYTHON.parent)]
    for candidate in (
        ROOT / "Tools" / "node",
        ROOT / "Tools",
        ROOT / "Tools" / "npm-global",
    ):
        if candidate.exists():
            additions.append(str(candidate))

    git_bin = str(cfg.get("git_bin") or "").strip()
    if git_bin and Path(git_bin).exists():
        additions.append(git_bin)

    env["PATH"] = os.pathsep.join(additions + [env.get("PATH", "")])
    if (ROOT / "Tools" / "npm-global").exists():
        env["NPM_CONFIG_PREFIX"] = str(ROOT / "Tools" / "npm-global")
    return env


def _lmstudio_cli(cfg):
    return Path(
        str(cfg.get("lmstudio_cli") or (Path.home() / ".lmstudio" / "bin" / "lms.exe"))
    )


def _start_lmstudio_server(cfg):
    if urlparse(cfg["api_base"]).hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    cli = _lmstudio_cli(cfg)
    if not cli.is_file():
        return False
    try:
        port = urlparse(cfg["api_base"]).port or 1234
        result = subprocess.run(
            [str(cli), "server", "start", "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
            timeout=30,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def recover_model(task, *, force=False):
    """Reload the task's LM Studio model with a rolling recovery budget."""
    cfg = settings()
    if cfg["provider"] != "lmstudio" or not cfg.get("auto_model_recovery", True):
        return False
    if urlparse(cfg["api_base"]).hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False

    state = read_json(task / "agent-state.json", {}) or {}
    error_text = (state.get("last_error") or "").lower()
    fatal = (
        "errordevicelost",
        "decode() failed",
        "channel error",
        "failed to evaluate",
        "out of memory",
        "connection refused",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "read timed out",
        "wall-clock limit",
    )
    if not force and not any(fragment in error_text for fragment in fatal):
        return False

    meta = read_json(task / "task.json", {}) or {}
    preferred = str(cfg.get("preferred_model") or "").strip()
    stored_id = str(meta.get("model") or "").strip()
    stored_key = str(meta.get("model_key") or "").strip()
    replacement = bool(preferred) and preferred.lower() not in {
        stored_id.lower(), stored_key.lower()
    }
    model_key = preferred if replacement else (stored_key or preferred)
    instance_id = preferred if replacement else (stored_id or preferred or model_key)
    cli = _lmstudio_cli(cfg)
    if not model_key or not cli.is_file():
        return False

    now = time.time()
    history = [
        float(stamp)
        for stamp in meta.get("model_recovery_history", [])
        if now - float(stamp) < 3600
    ]
    if len(history) >= 3:
        log(
            task,
            "system",
            "Model auto-recovery reached its rolling limit (3/hour); waiting instead of thrashing LM Studio.",
        )
        return False

    history.append(now)
    save_meta(
        task,
        model_recovery_history=history,
        activity="Recovering local model; task files and background jobs are preserved.",
    )
    log(task, "system", f"Reloading local model {model_key!r} after provider failure.")

    try:
        listed = requests.get(cfg["api_base"].rstrip("/") + "/models", timeout=5)
        listed.raise_for_status()
        if any(
            model.get("id") == instance_id for model in listed.json().get("data", [])
        ):
            unloaded = subprocess.run(
                [str(cli), "unload", instance_id],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=45,
                creationflags=NO_WINDOW,
            )
            if unloaded.returncode:
                raise RuntimeError(
                    "Could not unload the selected model instance; duplicate reload avoided."
                )
        args = [
            str(cli),
            "load",
            model_key,
            "--gpu",
            str(cfg.get("lmstudio_gpu_fraction", 0.8)),
            "--context-length",
            str(int(cfg.get("context_length", 16384))),
        ]
        if cfg.get("lmstudio_speculative_mtp", True):
            args += [
                "--speculative-draft-mtp",
                "--speculative-draft-max-tokens",
                "3",
            ]
        identifier = str(
            cfg.get("model_load_identifier") or instance_id
        ).strip()
        if identifier:
            args += ["--identifier", identifier]
        args += ["--yes"]

        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            creationflags=NO_WINDOW,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()[-1200:]
            raise RuntimeError("Model reload failed: " + detail)

        state["last_error"] = None
        atomic_json(task / "agent-state.json", state)
        return True
    except (
        OSError,
        RuntimeError,
        requests.RequestException,
        subprocess.TimeoutExpired,
    ) as error:
        log(task, "system", f"Model recovery failed: {error}")
        return False


def terminate_tree(process, preserve_jobs=True):
    """Stop one actor tree while preserving independently tracked jobs and live Blender."""
    try:
        parent = psutil.Process(process.pid)
    except psutil.Error:
        return

    preserve = owned_job_pids(ACTIVE) if preserve_jobs and ACTIVE else set()
    logger = _tracked_blender_logger()
    if logger:
        preserve.add(logger.pid)
    targets = []
    try:
        children = list(reversed(parent.children(recursive=True)))
    except psutil.Error:
        children = []

    for child in children:
        try:
            if child.pid in preserve:
                continue
            if child.name().lower() == "blender.exe" and not any(
                arg in ("-b", "--background") for arg in child.cmdline()
            ):
                # Never discard a user's interactive unsaved Blender scene on actor restart.
                continue
            targets.append(child)
        except psutil.Error:
            continue

    targets.append(parent)
    for target in targets:
        try:
            target.terminate()
        except psutil.Error:
            pass

    _, alive = psutil.wait_procs(targets, timeout=5)
    for target in alive:
        try:
            target.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=3)


def recover_orphan(meta):
    """Stop only a stale actor instance that belongs to this task/supervisor."""
    pid = meta.get("worker_pid")
    created = meta.get("worker_created")
    if not pid or not created:
        return
    try:
        process = psutil.Process(pid)
        command = " ".join(process.cmdline()).lower()
        owned = str(ROOT / "app" / "actor.py").lower() in command
        if abs(process.create_time() - float(created)) < 0.1 and owned:
            terminate_tree(process)
    except (psutil.Error, TypeError, ValueError):
        pass


def worker(task):
    global ACTIVE, PROC

    keep_awake(True)
    worker_failures = 0
    model_wait_failures = 0
    rounds = 0
    no_progress_passes = 0
    last_fingerprint = None
    reader_stop = None

    def accept_result(result):
        with LOCK:
            if STOP.is_set() or STEER.is_set() or not WAKE.is_set():
                return False
            generation = read_json(task / "task.json", {}).get("resume_generation", 0)
        try:
            validate_completion(task, result)
            with LOCK:
                if (
                    STOP.is_set()
                    or STEER.is_set()
                    or not WAKE.is_set()
                    or generation
                    != read_json(task / "task.json", {}).get("resume_generation", 0)
                    or time.time()
                    >= float(read_json(task / "task.json", {}).get("deadline", 0))
                ):
                    return False
                save_meta(task, status=result["status"], worker_pid=None)
                log(task, "assistant", result["summary"])
            return True
        except Exception as error:
            try:
                archive_json(task / "result.json", prefix="rejected-result")
            except OSError:
                pass
            log(
                task,
                "system",
                f"Completion rejected: {error}. Continue the ORIGINAL objective.",
            )
            return False

    try:
        meta = read_json(task / "task.json", {}) or {}
        if not (task / "acceptance.json").exists():
            initialize_contract(task, meta["objective"])

        recover_orphan(meta)
        save_meta(task, status="running", worker_pid=None)

        while not STOP.is_set():
            meta = read_json(task / "task.json", {}) or {}
            deadline = float(meta.get("deadline", 0))
            if not deadline or time.time() >= deadline:
                break

            if not WAKE.is_set():
                save_meta(task, status="paused")
                keep_awake(False)
                while not WAKE.wait(1) and not STOP.is_set():
                    if time.time() >= float(
                        (read_json(task / "task.json", {}) or {}).get("deadline", 0)
                    ):
                        break
                if STOP.is_set():
                    break
                if not WAKE.is_set():
                    break
                keep_awake(True)
                save_meta(task, status="running")
                continue

            result = read_json(task / "result.json")
            if result and accept_result(result):
                break
            if STOP.is_set() or not WAKE.is_set():
                continue
            deadline = float((read_json(task / "task.json", {}) or {}).get("deadline", 0))
            if time.time() >= deadline:
                break

            try:
                model = model_info()
            except Exception as error:
                model_wait_failures += 1
                if model_wait_failures == 1 or model_wait_failures % 10 == 0:
                    log(task, "system", f"Waiting for local model: {error}")
                save_meta(task, status="waiting_for_model", error=str(error))

                cfg = settings()
                if cfg["provider"] == "lmstudio" and cfg.get("auto_model_recovery", True):
                    if isinstance(error, requests.RequestException):
                        _start_lmstudio_server(cfg)
                    low = str(error).lower()
                    if any(
                        message in low
                        for message in (
                            "no llm is loaded",
                            "preferred_model is not loaded",
                            "selected model is not loaded",
                        )
                    ):
                        recover_model(task, force=True)

                STOP.wait(min(30, max(2, 2 * model_wait_failures)))
                continue

            # Model availability is a different failure domain from actor failures.
            model_wait_failures = 0
            save_meta(
                task,
                status="running",
                model=model["model"],
                model_key=model.get("model_key"),
                error=None,
            )

            rounds += 1
            log(
                task,
                "system",
                f"Working · pass {rounds} · {(deadline - time.time()) / 3600:.1f} hours available",
            )

            env = child_environment(task)
            env["TASK_CONTINUITY_CONTEXT_LENGTH"] = str(model["context"])
            env["TASK_CONTINUITY_VISION"] = "1" if model.get("vision", False) else "0"
            env["TASK_CONTINUITY_REASONING_OPTIONS"] = json.dumps(
                model.get("reasoning_options", [])
            )
            args = [
                str(PYTHON),
                str(ROOT / "app" / "actor.py"),
                str(task),
                model["model"],
            ]

            # A control request can arrive during model discovery. Recheck under
            # the same lock used by the UI before creating any reasoning process.
            with LOCK:
                if STOP.is_set() or not WAKE.is_set():
                    continue
                STEER.clear()  # The new actor reads all durable steering.
                PROC = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    creationflags=NO_WINDOW,
                    cwd=str(task),
                )
                proc = PROC
            try:
                worker_created = psutil.Process(proc.pid).create_time()
            except psutil.Error:
                worker_created = time.time()
            save_meta(
                task,
                worker_pid=proc.pid,
                worker_created=worker_created,
            )

            lines = queue.Queue(maxsize=256)
            reader_stop = threading.Event()

            def reader(stream, kind, output_queue=lines, stopped=reader_stop):
                def push(value):
                    while not stopped.is_set():
                        try:
                            output_queue.put(value, timeout=0.5)
                            return
                        except queue.Full:
                            continue

                try:
                    with stream:
                        for line in iter(lambda: stream.readline(65536), ""):
                            if stopped.is_set():
                                break
                            push((kind, line))
                finally:
                    push((kind, None))

            threading.Thread(
                target=reader, args=(proc.stdout, "out"), daemon=True
            ).start()
            threading.Thread(
                target=reader, args=(proc.stderr, "err"), daemon=True
            ).start()

            last_activity = time.monotonic()
            closed = set()
            tool_inflight = False
            cfg = settings()
            inference_stale = int(cfg.get("supervisor_inference_stale_seconds", 1200))
            tool_stale = int(cfg.get("supervisor_tool_stale_seconds", 2400))
            raw_limit = int(cfg.get("raw_log_max_mb", 50)) * 1024 * 1024

            while len(closed) < 2:
                current_meta = read_json(task / "task.json", {}) or {}
                deadline = float(current_meta.get("deadline", deadline))

                if STOP.is_set() or not WAKE.is_set() or time.time() >= deadline:
                    terminate_tree(
                        proc,
                        preserve_jobs=(not STOP.is_set() and time.time() < deadline),
                    )

                stale_limit = tool_stale if tool_inflight else inference_stale
                if time.monotonic() - last_activity > stale_limit:
                    log(
                        task,
                        "system",
                        "Agent stopped responding; restarting from durable task state.",
                    )
                    terminate_tree(proc)
                    break

                try:
                    kind, line = lines.get(timeout=1)
                except queue.Empty:
                    continue

                if line is None:
                    closed.add(kind)
                    continue

                last_activity = time.monotonic()
                append_text_line(
                    task / "raw-log.jsonl",
                    line,
                    max_bytes=raw_limit,
                )

                if kind == "err":
                    if any(
                        token in line.lower()
                        for token in ("error", "failed", "warning", "traceback")
                    ):
                        log(task, "system", line.strip()[:2000])
                    continue

                try:
                    event = json.loads(line)
                except ValueError:
                    continue

                event_type = event.get("type", "")
                item = event.get("item", {})
                item_type = item.get("type")

                if event_type == "item.started" and item_type in (
                    "mcp_tool_call",
                    "command_execution",
                ):
                    tool_inflight = True
                    log(
                        task,
                        "tool",
                        (item.get("tool") or item.get("command") or item_type)[:1500],
                    )
                elif event_type == "item.completed":
                    tool_inflight = False
                    if item_type == "agent_message":
                        log(task, "assistant", item.get("text", ""))
                    elif item_type == "mcp_tool_call":
                        log(
                            task,
                            "tool",
                            f"{item.get('tool', 'tool')} · {item.get('status', '')}\n"
                            + item.get("result_text", "")[:2000],
                        )
                    elif item_type == "command_execution":
                        log(
                            task,
                            "tool",
                            f"Command finished · exit {item.get('exit_code')}\n"
                            + item.get("aggregated_output", "")[:2500],
                        )
                elif event_type in ("error", "turn.failed"):
                    log(
                        task,
                        "system",
                        event.get("message")
                        or event.get("error", {}).get("message")
                        or str(event),
                    )
                elif event_type == "status":
                    save_meta(
                        task,
                        activity=event.get("text"),
                        last_activity=time.time(),
                    )

            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                terminate_tree(proc)
                try:
                    exit_code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    exit_code = -1

            PROC = None
            reader_stop.set()
            save_meta(task, worker_pid=None, worker_created=None)

            if STOP.is_set():
                stop_task_jobs(task)
                save_meta(task, status="stopped")
                break

            if time.time() >= deadline:
                stop_task_jobs(task)
                save_meta(
                    task,
                    status="time_limit",
                    error="Time budget reached. Saved outputs may be incomplete.",
                )
                break

            result = read_json(task / "result.json")
            if result and accept_result(result):
                break

            if not WAKE.is_set():
                continue

            if STEER.is_set():
                STEER.clear()
                worker_failures = 0
                continue

            state = read_json(task / "agent-state.json", {}) or {}
            if exit_code and repair_stalled_state(task):
                attempt = (
                    int(
                        (read_json(task / "task.json", {}) or {}).get(
                            "production_recovery_attempts", 0
                        )
                    )
                    + 1
                )
                save_meta(
                    task,
                    production_recovery_attempts=attempt,
                    activity=(
                        "Changing strategy after a no-progress loop; "
                        "preserving task files."
                    ),
                )
                log(
                    task,
                    "system",
                    f"Production recovery {attempt}/2: kept the goal/files and forced a different next action.",
                )
                worker_failures = 0
                continue

            state = read_json(task / "agent-state.json", {}) or {}
            if exit_code and state.get("stall_reason"):
                save_meta(
                    task,
                    status="blocked",
                    error=(
                        "Stopped after bounded recovery: no deliverable progress. "
                        "Existing files were preserved."
                    ),
                )
                break

            if exit_code and recover_model(task):
                worker_failures = 0
                continue

            contract = read_json(task / "acceptance.json", {}) or {}
            if contract.get("requires_action"):
                fingerprint = tuple(artifact_snapshot(task, contract))
                live_jobs = bool(owned_job_pids(task))
                if live_jobs:
                    no_progress_passes = 0
                elif fingerprint and fingerprint != last_fingerprint:
                    no_progress_passes = 0
                else:
                    no_progress_passes += 1
                last_fingerprint = fingerprint

                if no_progress_passes >= 3:
                    save_meta(
                        task,
                        status="blocked",
                        error=(
                            "Three worker restarts produced no durable output progress. "
                            "Stopped to avoid an unattended retry loop."
                        ),
                    )
                    log(
                        task,
                        "system",
                        "Stopped after repeated worker restarts without durable progress.",
                    )
                    break

            if exit_code:
                worker_failures += 1
                log(
                    task,
                    "system",
                    f"Agent process ended with error {exit_code}; recovering ({worker_failures}/5).",
                )
                if worker_failures >= 5:
                    save_meta(
                        task,
                        status="blocked",
                        error=(
                            "Repeated actor/provider failures. See the task log; "
                            "resume after fixing the cause."
                        ),
                    )
                    break
            else:
                worker_failures = 0
                log(
                    task,
                    "system",
                    "Actor exited without completion; checking durable state and continuing.",
                )

            STOP.wait(min(30, 3 + worker_failures * 3))

        else:
            # while condition is STOP-only; retained for structural clarity.
            pass

        if STOP.is_set():
            stop_task_jobs(task)
            save_meta(task, status="stopped")
        elif time.time() >= float(
            (read_json(task / "task.json", {}) or {}).get("deadline", 0)
        ):
            stop_task_jobs(task)
            save_meta(
                task,
                status="time_limit",
                error="Time budget reached. Progress saved; Resume adds more time.",
            )
    except Exception as error:
        save_meta(task, status="blocked", error=str(error), worker_pid=None)
        log(task, "system", traceback.format_exc())
    finally:
        if reader_stop:
            reader_stop.set()
        if PROC:
            terminate_tree(PROC, preserve_jobs=False)
            try:
                PROC.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if (read_json(task / "task.json", {}) or {}).get("status") in (
            "blocked",
            "stopped",
            "time_limit",
        ):
            stop_task_jobs(task)
        keep_awake(False)
        PROC = None
        with LOCK:
            ACTIVE = None
            # A follow-up may arrive after completion was accepted but before
            # this worker released its PC-control slot. Keep that queued work.
            if (
                not STOP.is_set()
                and read_json(task / "task.json", {}).get("status") == "queued"
            ):
                launch_task(task)


def launch_task(task):
    global ACTIVE
    with LOCK:
        if ACTIVE:
            raise ValueError(
                "One agent controls the PC at a time. Stop or finish the current task first."
            )
        STOP.clear()
        STEER.clear()
        WAKE.set()
        ACTIVE = task
        threading.Thread(target=worker, args=(task,), daemon=True).start()


def list_tasks():
    tasks = []
    for path in (DATA_ROOT / "Tasks").glob("*/task.json"):
        meta = read_json(path)
        if not isinstance(meta, dict):
            continue
        task_id = meta.get("id")
        # A stray/corrupt diagnostic folder must never crash the whole chat server.
        if (
            not isinstance(task_id, str)
            or not task_id
            or not all(c in "0123456789abcdef-" for c in task_id)
        ):
            continue
        if path.parent.name != task_id:
            continue
        tasks.append(meta)
    return sorted(tasks, key=lambda m: m.get("created", 0), reverse=True)


def find_task(task_id):
    if not task_id or not all(c in "0123456789abcdef-" for c in task_id):
        raise ValueError("Invalid task ID.")
    task = DATA_ROOT / "Tasks" / task_id
    if not (task / "task.json").is_file():
        raise ValueError("Task not found.")
    return task


def task_hours(value=10):
    if isinstance(value, bool):
        raise ValueError("Time budget must be a number of hours.")
    try:
        hours = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Time budget must be a number of hours.") from error
    if not math.isfinite(hours) or not 0.1 <= hours <= 72:
        raise ValueError("Time budget must be between 0.1 and 72 hours.")
    return hours


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def send(self, code, data, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(
            (
                json.dumps(data, ensure_ascii=False)
                if content_type == "application/json"
                else data
            ).encode("utf-8")
        )

    def valid_host(self):
        return self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self.valid_host():
            self.send(403, {"error": "Loopback host only."})
            return
        url = urlparse(self.path)
        if url.path == "/":
            html = (
                (ROOT / "app/index.html")
                .read_text(encoding="utf-8")
                .replace("__TOKEN__", TOKEN)
            )
            self.send(200, html, "text/html")
        elif url.path == "/guide":
            self.send(
                200,
                (ROOT / "docs/start-here.html").read_text(encoding="utf-8"),
                "text/html",
            )
        elif url.path == "/api/status":
            try:
                model = model_info()
            except Exception as error:
                model = {"error": str(error)}
            self.send(
                200,
                {
                    "model": model,
                    "tasks": list_tasks(),
                    "active": ACTIVE.name if ACTIVE else None,
                },
            )
        elif url.path == "/api/health":
            self.send(
                200,
                {
                    "app_id": "task-continuity-agent",
                    "source_root": str(ROOT),
                    "port": PORT,
                },
            )
        elif url.path.startswith("/api/task/"):
            try:
                task = find_task(url.path.rsplit("/", 1)[-1])
                events = []
                if (task / "events.jsonl").exists():
                    # Keep refresh bounded even after hours of output.
                    data = tail_bytes(task / "events.jsonl", 160000)
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        try:
                            events.append(json.loads(line))
                        except ValueError:
                            pass
                self.send(
                    200,
                    {
                        "task_folder": str(task.resolve()),
                        "meta": read_json(task / "task.json"),
                        "events": events,
                        "progress": read_json(task / "progress.json"),
                        "result": read_json(task / "result.json"),
                    },
                )
            except ValueError as error:
                self.send(404, {"error": str(error)})
        else:
            self.send(404, {"error": "Not found."})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if (
            not self.valid_host()
            or (
                origin is not None
                and origin not in (ORIGIN, f"http://localhost:{PORT}")
            )
            or self.headers.get("X-Agent-Token") != TOKEN
        ):
            self.send(403, {"error": "Open the agent page locally to control it."})
            return
        # Resume/follow-up requests must not partially mutate another task before
        # discovering that the single PC-control slot is already occupied.
        with LOCK:
            self.post_action()

    def post_action(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 100000:
                raise ValueError("Invalid request size.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Request must contain a JSON object.")
            # Validate before writing steering, results, or task metadata.
            if self.path in ("/api/start", "/api/resume", "/api/followup"):
                hours = task_hours(data.get("hours", 10))
            if self.path == "/api/start":
                objective = str(data.get("task", "")).strip()
                if not objective:
                    raise ValueError("Enter a task.")
                with LOCK:
                    if ACTIVE:
                        raise ValueError(
                            "Another task is running. Add a follow-up, or stop it first."
                        )
                    task_id = str(uuid.uuid4())
                    task = DATA_ROOT / "Tasks" / task_id
                    task.mkdir()
                    now = time.time()
                    atomic_json(
                        task / "task.json",
                        {
                            "id": task_id,
                            "objective": objective,
                            "hours": hours,
                            "created": now,
                            "deadline": now + hours * 3600,
                            "status": "queued",
                            "internal_test": bool(data.get("internal_test", False)),
                        },
                    )
                    (task / "objective.md").write_text(objective, encoding="utf-8")
                    initialize_contract(task, objective)
                    # The task directory gets concise shared rules; historical custom skills aren't loaded.
                    (task / "AGENTS.md").write_text(
                        (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
                    log(task, "user", objective)
                    launch_task(task)
                self.send(200, {"id": task_id})
                return
            task = find_task(data.get("id"))
            if (
                self.path in ("/api/resume", "/api/followup")
                and ACTIVE
                and ACTIVE != task
            ):
                raise ValueError(
                    "Another task is running. Stop or finish it before resuming this task."
                )
            if self.path == "/api/open":
                p = Path(data.get("path", "")).resolve()
                if not p.is_relative_to(task.resolve()) or not p.is_file():
                    raise ValueError(
                        "Open an existing file in the selected task folder."
                    )
                if p.suffix.lower() not in (
                    ".blend",
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".mp4",
                    ".txt",
                    ".md",
                    ".html",
                    ".pdf",
                ):
                    raise ValueError("Open this file manually from its folder.")
                os.startfile(str(p))
            elif self.path == "/api/stop":
                if ACTIVE == task:
                    STOP.set()
                    WAKE.set()
                    if PROC:
                        STEER.set()
                        terminate_tree(PROC, preserve_jobs=False)
                else:
                    save_meta(task, status="stopped")
                stop_task_jobs(task)
                log(task, "system", "Stop requested. Saved progress is retained.")
            elif self.path == "/api/pause":
                if ACTIVE == task:
                    WAKE.clear()
                    if PROC:
                        terminate_tree(PROC)
                    log(
                        task,
                        "system",
                        "Agent paused. Already-started background jobs may finish; Stop cancels them. Resume uses saved context.",
                    )
            elif self.path == "/api/resume":
                meta = read_json(task / "task.json", {})
                generation = meta.get("resume_generation", 0) + 1
                # Archive first: a locked result must not leave the task queued
                # with an old completion that would immediately end the resume.
                if ACTIVE != task or meta.get("status") in (
                    "completed",
                    "blocked",
                    "stopped",
                    "time_limit",
                ):
                    archive_json(task / "result.json")
                save_meta(
                    task,
                    deadline=time.time() + hours * 3600,
                    status="queued",
                    resume_generation=generation,
                    production_recovery_attempts=0,
                )
                if ACTIVE == task:
                    # Running worker reads updated budget in next pass; pause keeps original deadline otherwise.
                    WAKE.set()
                else:
                    launch_task(task)
            elif self.path == "/api/followup":
                text = str(data.get("text", "")).strip()
                if not text:
                    raise ValueError("Enter a follow-up instruction.")
                meta = read_json(task / "task.json", {})
                if ACTIVE == task:
                    STEER.set()
                    if PROC:
                        terminate_tree(PROC)
                archive_json(task / "result.json")
                instructions = read_json(task / "steering.json", [])
                instructions.append(text)
                atomic_json(task / "steering.json", instructions)
                log(task, "user", text)
                if ACTIVE == task:
                    # Interrupt at the process boundary, then feed exact user steering to the same task.
                    changes = {
                        "session_id": None,
                        "resume_generation": meta.get("resume_generation", 0) + 1,
                    }
                    if meta.get("status") in (
                        "completed",
                        "blocked",
                        "stopped",
                        "time_limit",
                    ):
                        changes.update(
                            status="queued", deadline=time.time() + hours * 3600
                        )
                    save_meta(task, **changes)
                    WAKE.set()
                else:
                    save_meta(
                        task,
                        deadline=time.time() + hours * 3600,
                        status="queued",
                        resume_generation=meta.get("resume_generation", 0) + 1,
                        production_recovery_attempts=0,
                    )
                    launch_task(task)
            else:
                raise ValueError("Unknown action.")
            self.send(200, {"ok": True})
        except Exception as error:
            self.send(400, {"error": str(error)})


def main():
    standard_user()
    ensure_data_layout()
    # Singleton per user. Relaunching the shortcut opens the existing chat.
    ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
    identity = hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:16]
    mutex = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Local\\TaskContinuityAgentUI-" + identity
    )
    if not mutex:
        raise OSError("Could not acquire the supervisor singleton.")
    if ctypes.windll.kernel32.GetLastError() == 183:
        if "--no-browser" not in sys.argv:
            os.startfile(ORIGIN)
        return
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    atomic_json(
        DATA_ROOT / "State/chat-server.json",
        {"pid": os.getpid(), "url": ORIGIN, "started": time.time()},
    )
    # Restart an interrupted task, never a completed/blocked/paused task.
    for meta in list_tasks():
        if (
            meta.get("status") in ("running", "queued", "waiting_for_model")
            and meta.get("deadline", 0) <= time.time()
        ):
            task = find_task(meta["id"])
            recover_orphan(meta)
            stop_task_jobs(task)
            save_meta(
                task,
                status="time_limit",
                worker_pid=None,
                error="Time budget expired while the supervisor was offline.",
            )
            continue
        if (
            meta.get("status") in ("running", "queued", "waiting_for_model")
            and meta.get("deadline", 0) > time.time()
        ):
            launch_task(find_task(meta["id"]))
            break
    if "--no-browser" not in sys.argv:
        os.startfile(ORIGIN)
    server.serve_forever()


if __name__ == "__main__":
    main()
