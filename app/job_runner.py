"""Durable standard-user background command runner.

The runner is intentionally independent from the reasoning process. A model/actor restart
must not kill a legitimate long job, while an explicit stop/deadline must kill the whole
owned child tree before a terminal result is published.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from common import (
    NO_WINDOW,
    atomic_json,
    read_json,
    standard_user,
    settings,
    rotate_file,
)


def _terminate_tree(
    process: subprocess.Popen, grace_seconds: float = 5, known_children=()
) -> None:
    try:
        parent = psutil.Process(process.pid) if process.poll() is None else None
    except psutil.Error:
        parent = None

    try:
        children = parent.children(recursive=True) if parent else []
    except psutil.Error:
        children = []

    targets = list(
        {child.pid: child for child in [*known_children, *children]}.values()
    )
    if parent:
        targets.append(parent)
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


def _copy_log(stream, path, maximum, errors, losses=None, tolerate_io_errors=False):
    """Drain fixed-size chunks so newline-free output cannot exhaust memory/disk."""
    try:
        with stream:
            while chunk := stream.read1(65536):
                if not rotate_file(path, maximum, keep=2):
                    # Keep draining the child pipe without exceeding the disk
                    # budget when a log viewer temporarily blocks rotation.
                    if losses is not None:
                        losses["dropped_bytes"] = losses.get("dropped_bytes", 0) + len(
                            chunk
                        )
                    continue
                try:
                    with path.open("ab") as output:
                        output.write(chunk)
                        output.flush()
                except OSError as error:
                    if (
                        not isinstance(error, PermissionError)
                        and not tolerate_io_errors
                    ):
                        raise
                    # A viewer/antivirus may deny appends temporarily. Keep draining
                    # and resume persistence on the next chunk instead of killing work.
                    if losses is not None:
                        losses["dropped_bytes"] = losses.get("dropped_bytes", 0) + len(
                            chunk
                        )
    except Exception as error:
        errors.append(error)


def _spawn_owned(spec):
    """Attach the suspended Windows child to a kill-on-close job before it can run.

    Killing a runner during the spawn/ledger gap must not orphan its command tree.
    The independent runner keeps the job handle across actor/supervisor restarts.
    """
    job = None
    if os.name == "nt":
        import win32job

        job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        limits["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, limits
        )
    process = None
    try:
        process = subprocess.Popen(
            spec["argv"],
            cwd=spec["cwd"],
            env=spec.get("env") or None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=NO_WINDOW | (0x00000004 if job else 0),
            close_fds=True,
        )
        if job:
            import win32api

            handle = win32api.OpenProcess(0x0101, False, process.pid)
            try:
                win32job.AssignProcessToJobObject(job, handle)
                psutil.Process(process.pid).resume()
            finally:
                handle.Close()
        return process, job
    except Exception:
        if process is not None:
            process.kill()
            process.wait(timeout=5)
        if job:
            job.Close()
        raise


def _load_spec(folder: Path) -> dict:
    spec = read_json(folder / "job.json")
    if not isinstance(spec, dict):
        raise RuntimeError("Missing or corrupt job.json.")
    argv = spec.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
    ):
        raise RuntimeError("Invalid job argv.")
    cwd = Path(str(spec.get("cwd", "")))
    if not cwd.is_dir():
        raise RuntimeError(f"Job working directory does not exist: {cwd}")
    timeout = int(spec.get("timeout_seconds", 0))
    if timeout < 1:
        raise RuntimeError("Invalid job timeout.")
    return spec


def main(folder: str | Path) -> None:
    standard_user()
    folder = Path(folder).resolve()
    process: subprocess.Popen | None = None
    job = None
    children = {}
    pumps = []
    log_errors = []
    log_losses = {name: {} for name in ("stdout", "stderr")}
    result = None

    try:
        spec = _load_spec(folder)
        atomic_json(
            folder / "runner.json",
            {
                "pid": os.getpid(),
                "created": psutil.Process().create_time(),
            },
        )

        if read_json(folder / "cancel.json"):
            raise RuntimeError("Job cancelled before launch")
        task_file = folder.parent.parent / "task.json"
        task_deadline = (read_json(task_file, {}) or {}).get("deadline")
        if task_deadline and float(task_deadline) <= time.time():
            raise subprocess.TimeoutExpired(spec["argv"], spec["timeout_seconds"])
        maximum = int(settings()["raw_log_max_mb"]) * 1024 * 1024
        process, job = _spawn_owned(spec)
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            pump = threading.Thread(
                target=_copy_log,
                args=(
                    stream,
                    folder / (name + ".log"),
                    maximum,
                    log_errors,
                    log_losses[name],
                ),
                daemon=True,
            )
            pump.start()
            pumps.append(pump)
        try:
            created = psutil.Process(process.pid).create_time()
        except psutil.NoSuchProcess:
            if process.poll() is None:
                raise
        else:
            atomic_json(
                folder / "process.json", {"pid": process.pid, "created": created}
            )

        timeout_deadline = time.monotonic() + int(spec["timeout_seconds"])

        while True:
            # Retain only live descendants. A busy command may create millions of
            # short-lived children over a multi-day run; exited PIDs need no cleanup.
            children = {
                pid: child for pid, child in children.items() if child.is_running()
            }
            try:
                children.update(
                    {
                        child.pid: child
                        for child in psutil.Process(process.pid).children(
                            recursive=True
                        )
                    }
                )
            except psutil.Error:
                pass
            if read_json(folder / "cancel.json"):
                raise RuntimeError("Job cancelled")
            if log_errors:
                raise RuntimeError(f"Could not persist job output: {log_errors[0]}")
            remaining = timeout_deadline - time.monotonic()
            task_meta = read_json(task_file, {}) or {}
            task_deadline = task_meta.get("deadline")
            if task_deadline:
                remaining = min(remaining, float(task_deadline) - time.time())

            if remaining <= 0:
                raise subprocess.TimeoutExpired(spec["argv"], spec["timeout_seconds"])

            try:
                exit_code = process.wait(timeout=min(0.5, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        result = {"exit_code": exit_code}
    except subprocess.TimeoutExpired:
        result = {"exit_code": -1, "error": "Job time limit reached"}
    except Exception as error:
        result = {"exit_code": -1, "error": f"{type(error).__name__}: {error}"}
    finally:
        # Close the Windows ownership handle before publishing any terminal state,
        # including errors/timeouts. It also kills descendants missed between polls.
        if job:
            job.Close()
        if process is not None:
            _terminate_tree(process, known_children=children.values())
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for pump in pumps:
            pump.join(timeout=5)
        if result is not None:
            dropped = {
                name: info["dropped_bytes"]
                for name, info in log_losses.items()
                if info.get("dropped_bytes")
            }
            if dropped:
                result["log_dropped_bytes"] = dropped
            if log_errors or any(pump.is_alive() for pump in pumps):
                result["exit_code"] = -1
                result["error"] = (
                    result.get("error") or "Job output did not finish cleanly"
                )
            if read_json(folder / "cancel.json"):
                result["exit_code"] = -1
                result["error"] = "Job cancelled"
            result["finished_at"] = time.time()
            atomic_json(folder / "result.json", result)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: job_runner.py <job-folder>")
    main(sys.argv[1])
