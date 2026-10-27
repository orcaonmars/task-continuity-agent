# GitHub publishing

The public source release is ready to be reviewed and published by its owner. A local build does not create a repository, push commits, or validate hosted Actions.

1. Run `scripts/build.ps1`.
2. Create a repository named, for example, `task-continuity-agent`.
3. Extract `dist/task-continuity-agent-1.0.0.zip` into an empty folder and upload its contents, or use Git after reviewing staged files. The ZIP is the safest manual-upload path.
4. Never upload `data/`, `.venv/`, browser profiles, local configuration, model files, or backups. Web uploads do not apply `.gitignore` automatically.
5. Confirm the Windows Actions matrix passes. Enable Issues and private Security Advisories.
6. Create the v1.0.0 release and attach the ZIP, `dist/SHA256SUMS.txt`, and `dist/release-manifest.json`. Use the verified scope and limits from `RELEASE-STATUS.md` in the description.

This is an experimental Windows source distribution. Do not present it as a one-click executable, a sandbox, or a guarantee of perfect output or overnight success. Python and a separately installed local model are required.

This project is a standalone Windows application distributed as source. The archive includes the installer, normal launcher, tests, and documentation.
