"""Independent live Blender owner with bounded diagnostic pipe draining."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from common import NO_WINDOW, atomic_json, standard_user
from job_runner import _copy_log


def main(ledger, maximum, argv, launch_id):
    standard_user()
    ledger = Path(ledger)
    log = ledger.parent.parent / "Logs" / "blender-startup.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "logger_pid": os.getpid(),
        "logger_created": psutil.Process().create_time(),
        "started": time.time(),
        "launch_id": launch_id,
    }
    atomic_json(ledger, state)
    process = None
    pump = None
    errors = []
    losses = {}
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW,
            close_fds=True,
        )
        pump = threading.Thread(
            target=_copy_log,
            args=(process.stdout, log, maximum, errors, losses, True),
            daemon=True,
        )
        pump.start()
        state.update(pid=process.pid, created=psutil.Process(process.pid).create_time())
        atomic_json(ledger, state)
        state["exit_code"] = process.wait()
    except Exception as error:
        state["error"] = f"{type(error).__name__}: {error}"
        # Logging/ledger failures must not discard a live unsaved Blender scene.
        if process is not None:
            state["exit_code"] = process.wait()
    finally:
        if pump:
            pump.join(timeout=5)
        if errors:
            state["logging_error"] = str(errors[0])[:500]
        if losses.get("dropped_bytes"):
            state["log_dropped_bytes"] = losses["dropped_bytes"]
        state["finished_at"] = time.time()
        atomic_json(ledger, state)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(
            "Usage: blender_launcher.py <ledger> <log-bytes> <argv-json> <launch-id>"
        )
    main(sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3]), sys.argv[4])
