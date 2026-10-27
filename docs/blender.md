# Blender capability

Blender is optional and disabled by default.

## Background mode

The agent can run isolated Blender Python jobs, inspect projects, import assets, render previews, render resumable frame sequences, and encode/review output when the Blender executable is available. This path does **not** require the live bridge add-on.

Set `capabilities.blender` to `true`, and set `blender_exe` if Blender is not on PATH.

## Live Blender bridge

The live `blender` and `ensure_blender` socket tools use the Blender MCP add-on and its local JSON server. A fresh public install does not silently install third-party Blender add-ons into your Blender profile.

If you enable live Blender control, install and enable a compatible Blender MCP add-on yourself. The configured port defaults to `9876` and can be changed with `blender_port`.

## Video verification

Final video acceptance uses `ffprobe` when video output is required. Put `ffprobe` on PATH or install ffmpeg in a standard location.

Run `scripts\doctor.ps1` to see which optional capabilities are ready.