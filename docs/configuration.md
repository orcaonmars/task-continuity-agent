# Advanced configuration

The installer creates `data/config.json` from the defaults in `config.example.json`. Edit the local file; restart the agent after changing configuration so the supervisor and tools use the same settings. Invalid values produce an explicit configuration error.

## Provider and model

The default `provider` is `"lmstudio"`, with `api_base` set to `"http://127.0.0.1:1234/v1"`. Load a model trained for tool use in LM Studio. Another local server can use `provider: "openai-compatible"`; it must provide `/models` and streaming `/chat/completions` with compatible tool-call messages. An API label alone does not establish compatibility with every model or backend. Try a short write/read task first.

With `preferred_model: ""`, a new task selects the first available model and saves that choice. Recovery keeps the task's selected model even if the server lists models in a different order. If it disappears, reload it or set `preferred_model` explicitly; the agent does not silently choose another model. An explicit `preferred_model` overrides the saved choice. LM Studio accepts a model key or loaded instance identifier; the compatible provider uses a model identifier.

Use a model and backend that accept image inputs for visual work. The compatible provider's `vision` setting defaults to `false`; set it to `true` only when supported. LM Studio discovery reports the loaded model's vision capability. The supervisor passes that capability to the actor. Text-only models keep text/DOM workflows, omit image inputs, and cannot use image review or visual quality approval. A configuration flag cannot give a text-only model visual judgment.

`context_length` defaults to `16384`; `max_output_tokens` defaults to `4096` and is capped at half the context setting. LM Studio can report the loaded instance's actual context length. Backend-specific sampling fields belong in `request_overrides`; reserved model, messages, tools, and stream fields stay agent-controlled.

## Time budgets and recovery

The interface accepts **0.1–72 hours**, including **50 hours**. This is a maximum, and verified completion ends work early. Pause keeps the current deadline; Resume uses saved progress and sets a fresh deadline from now using the selected budget. A follow-up to an active task keeps its current deadline; a follow-up to an inactive task starts a fresh budget.

| Setting | Default | Purpose |
| --- | ---: | --- |
| `prediction_timeout_seconds` | 900 | Maximum time for one streamed model response |
| `stream_read_timeout_seconds` | 600 | Maximum wait between received stream data |
| `tool_timeout_seconds` | 2100 | Wait for one tool response |
| `supervisor_inference_stale_seconds` | 1200 | Supervisor threshold for an unresponsive model step |
| `supervisor_tool_stale_seconds` | 2400 | Supervisor threshold for an unresponsive tool step |

Tracked shell jobs return promptly and run independently; the tool-response timeout is not their runtime limit. Shell jobs can request up to **259200 seconds (72 hours)**, and frame-render jobs allow up to 72 hours. The task deadline still applies to both. Individual commands may use shorter defaults.

`auto_model_recovery` defaults to `true`. For LM Studio it permits automatic server startup and bounded model reload attempts. Set it to `false` to leave startup and loading to you, especially when sharing LM Studio. Other compatible providers are not automatically restarted. Optional LM Studio settings include `lmstudio_cli`, `model_load_identifier`, `lmstudio_gpu_fraction` (default `0.8`), and `lmstudio_speculative_mtp` (default `true`).

## Capabilities and storage

Core file, shell, job, brief, checkpoint, and completion tools stay available. `capabilities` controls the additional `web`, `desktop`, `media`, `blender`, and `extra` groups. Blender defaults to `false`; the other groups default to `true`. These switches do not sandbox shell commands.

Extra MCP servers are configured under `extra_mcp_servers` with a `command`, string `args`, and optional string `env` values. They connect only when the extra group is requested. A missing executable, failed initialization, or invalid tool definition is recorded for that server while core tools and other working servers remain available. Review each server's authority and installation requirements before enabling it.

| Setting | Default | Scope |
| --- | ---: | --- |
| `max_download_mb` | 4096 | Per direct HTTP or requested browser download |
| `event_log_max_mb` | 20 | Rotation threshold for event logs |
| `raw_log_max_mb` | 50 | Rotation threshold for tracked job/actor output |
| `backups_per_file` | 20 | Retained pre-edit copies per file edited through file tools |

Rotation retains a small number of previous logs. Live Blender uses an independent bounded log helper, and transient diagnostic locks do not terminate a useful job. Dropped output byte counts are retained where possible. Large tool responses are preserved under task-local `ContextArchive/ToolResults` with an explicit pointer in the active conversation. Browser scratch is reclaimed only with matching ownership receipts and confirmed-dead owners. Browser transfer monitoring can briefly overshoot its limit before cancellation. Shell commands and third-party programs do not inherit file-tool backups or download limits. Outputs, completed downloads, context archives, and old tasks remain on disk for recovery; there is **no total storage quota**. Check free space and remove old task folders when no longer needed.

The interface uses loopback `ui_port: 8765`; select another port if occupied. The browser defaults to installed Chrome; `browser_channel: "msedge"` selects Edge. See [Blender setup](blender.md) for `blender_exe`, the optional live bridge, and `blender_port`.
