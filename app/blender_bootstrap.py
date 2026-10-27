"""Start the optional live Blender bridge with bounded recovery.

This script is executed only in Blender instances launched by Task Continuity Agent.
If the compatible bridge add-on is missing or repeatedly fails, the launched instance
exits rather than lingering forever in a half-started state.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import bpy

DATA_ROOT = Path(
    os.environ.get(
        "TASK_CONTINUITY_DATA",
        str(Path.home() / ".task-continuity-agent"),
    )
)
LOG_PATH = DATA_ROOT / "Logs" / "blender-bootstrap-errors.log"
PORT = int(os.environ.get("TASK_CONTINUITY_BLENDER_PORT", "9876"))
MAX_ATTEMPTS = 8
LOG_MAX_BYTES = 1024 * 1024
attempts = 0


def log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size >= LOG_MAX_BYTES:
            older = LOG_PATH.with_name(LOG_PATH.name + ".1")
            oldest = LOG_PATH.with_name(LOG_PATH.name + ".2")
            oldest.unlink(missing_ok=True)
            if older.exists():
                older.replace(oldest)
            LOG_PATH.replace(older)
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(message[-16384:].rstrip() + "\n")
    except OSError:
        # Diagnostics must not interrupt bounded startup/quit behavior when locked.
        pass


def stop_launched_blender() -> None:
    try:
        bpy.ops.wm.quit_blender()
    except Exception:
        # Last-resort clean process exit for an agent-owned startup instance.
        os._exit(2)


try:
    addon_dir = (
        Path.home()
        / "AppData"
        / "Roaming"
        / "Blender Foundation"
        / "Blender"
        / f"{bpy.app.version[0]}.{bpy.app.version[1]}"
        / "scripts"
        / "addons"
    )
    if str(addon_dir) not in sys.path:
        sys.path.insert(0, str(addon_dir))

    import addon_utils

    # Task-local startup glue only. Do not rewrite persistent Blender preferences.
    addon_utils.enable("blender_mcp", default_set=False, persistent=True)
    import blender_mcp

    scene = bpy.context.scene
    scene.blendermcp_port = PORT
    scene.blendermcp_auto_start_server = True
    scene.blendermcp_use_polyhaven = True
    blender_mcp._user_stopped_server = False
except Exception:
    log("Bridge bootstrap import/configuration failed:\n" + traceback.format_exc())
    bpy.app.timers.register(stop_launched_blender, first_interval=0.1)
else:

    def ensure_bridge():
        global attempts
        attempts += 1
        try:
            result = blender_mcp._blendermcp_ensure_server_running()
            if result is False:
                raise RuntimeError(
                    "Blender MCP reported that its server is not running."
                )
            if attempts > 1:
                log(f"Bridge started successfully on attempt {attempts}.")
            return None
        except Exception:
            detail = traceback.format_exc()
            if attempts == 1 or attempts >= MAX_ATTEMPTS:
                log(
                    f"Bridge startup attempt {attempts}/{MAX_ATTEMPTS} failed:\n"
                    + detail
                )
            if attempts >= MAX_ATTEMPTS:
                log(
                    "Giving up on the agent-launched Blender bridge; closing this instance."
                )
                bpy.app.timers.register(stop_launched_blender, first_interval=0.1)
                return None
            return min(10.0, 0.5 * (2 ** (attempts - 1)))

    # Let Blender finish UI initialization before opening the socket.
    bpy.app.timers.register(ensure_bridge, first_interval=0.5, persistent=False)
