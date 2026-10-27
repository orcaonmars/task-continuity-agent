# Contributing

Keep changes small, testable, and provider-agnostic. Do not add developer-specific absolute paths, credentials, task history, browser profiles, generated model files, or runtime state.

Before submitting:

1. Run `powershell -ExecutionPolicy Bypass -File .\scripts\validate.ps1`.
2. Add or update a regression test for behavior changes.
3. Preserve the durable task identity and completion-verification boundaries.
4. Keep domain integrations behind capability groups when they add substantial tool schema.
