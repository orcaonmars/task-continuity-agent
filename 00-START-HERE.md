# Start here

1. Open PowerShell in this folder and run `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1` as a normal Windows user.
2. Load a tool-capable local model in LM Studio, or configure another local OpenAI-compatible endpoint in `data/config.json`.
3. Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\start-agent.ps1`, or use the Desktop shortcut. The watchdog is the normal launch path.
4. Try a short text-file task, then use the example briefs in `docs/example-tasks.md`.
5. For Blender, enable `capabilities.blender`; use a vision-capable model and read `docs/blender.md`.

If the default UI port is occupied, choose another `ui_port` in the local config. The launcher prints the actual address and does not take over another installation.

Runtime task data and the isolated environment are private local files, excluded from the release.

For GitHub, upload the public source from the release ZIP. Never drag the entire installed folder into the web uploader: Git ignore rules do not protect a manual upload. Follow `docs/publishing.md` and review `RELEASE-STATUS.md` first.
