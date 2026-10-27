"""Shared configuration, persistence, path, and process helpers.

This module intentionally stays small and dependency-light because every long-running
component imports it. Reliability here matters more than cleverness.
"""

from __future__ import annotations

import ctypes
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import psutil

APP_DIR = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("TASK_CONTINUITY_ROOT", APP_DIR.parent)).resolve()
DATA_ROOT = Path(os.environ.get("TASK_CONTINUITY_DATA", ROOT / "data")).resolve()
CONFIG = Path(
    os.environ.get("TASK_CONTINUITY_CONFIG", DATA_ROOT / "config.json")
).resolve()
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CONFIG_VERSION = 2

_venv_python = (
    ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
)
PYTHON = _venv_python if _venv_python.is_file() else Path(sys.executable).resolve()

DEFAULT_SETTINGS = {
    "config_version": CONFIG_VERSION,
    "api_base": "http://127.0.0.1:1234/v1",
    "provider": "lmstudio",
    "preferred_model": "",
    "model_load_identifier": "",
    "lmstudio_cli": "",
    "vision": False,
    "context_length": 16384,
    "max_output_tokens": 4096,
    "temperature": 0.6,
    "top_p": 0.9,
    "top_k": 20,
    "prediction_timeout_seconds": 900,
    "stream_read_timeout_seconds": 600,
    "tool_timeout_seconds": 2100,
    "supervisor_inference_stale_seconds": 1200,
    "supervisor_tool_stale_seconds": 2400,
    "auto_model_recovery": True,
    "lmstudio_gpu_fraction": 0.8,
    "lmstudio_speculative_mtp": True,
    "ui_port": 8765,
    "browser_channel": "chrome",
    "blender_exe": "",
    "blender_port": 9876,
    "git_bin": "",
    "max_download_mb": 4096,
    "event_log_max_mb": 20,
    "raw_log_max_mb": 50,
    "backups_per_file": 20,
    "request_overrides": {},
    "capabilities": {
        "web": True,
        "desktop": True,
        "media": True,
        "blender": False,
        "extra": True,
    },
    "extra_mcp_servers": {},
}


def file_sha256(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tail_bytes(path: str | Path, limit: int) -> bytes:
    path = Path(path)
    limit = max(0, int(limit))
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read(limit)


def _pending_jsons(path: str | Path) -> list[Path]:
    path = Path(path)
    try:
        return list(path.parent.glob(path.name + ".pending-*.json"))
    except OSError:
        return []


def atomic_json(path: str | Path, value) -> bool:
    """Durably replace JSON.

    Windows can temporarily deny os.replace when another process has the target open.
    In that case the newest state is retained as a pending sidecar; read_json prefers
    the newest valid state and a future successful write cleans old sidecars.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.time_ns()
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    data = json.dumps(value, indent=2, ensure_ascii=False)

    try:
        with tmp.open("w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

        for attempt in range(40):
            try:
                os.replace(tmp, path)
                for pending in _pending_jsons(path):
                    try:
                        marker = int(
                            pending.name.split(".pending-", 1)[1].split("-", 1)[0]
                        )
                        if marker <= stamp:
                            pending.unlink(missing_ok=True)
                    except (OSError, ValueError, IndexError):
                        continue
                return True
            except PermissionError:
                time.sleep(min(0.2, 0.02 * (attempt + 1)))

        pending = path.with_name(
            path.name + f".pending-{stamp}-" + uuid.uuid4().hex + ".json"
        )
        try:
            os.replace(tmp, pending)
        except OSError:
            try:
                tmp.rename(pending)
            except OSError as error:
                raise PermissionError(
                    f"Could not persist {path}; target and fallback were locked."
                ) from error
        # A persistently locked target must not accumulate an unbounded number of
        # full context snapshots. Keep three recent recovery copies; preserve any
        # concurrently-created state newer than this write.
        older = []
        for candidate in _pending_jsons(path):
            try:
                marker = int(candidate.name.split(".pending-", 1)[1].split("-", 1)[0])
                if marker <= stamp:
                    older.append((marker, candidate))
            except (ValueError, IndexError):
                continue
        for _, candidate in sorted(older, reverse=True)[3:]:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def read_json(path: str | Path, default=None):
    """Read the newest valid main or pending JSON state."""
    path = Path(path)
    candidates = [path, *_pending_jsons(path)]
    valid: list[tuple[int, Path]] = []
    for candidate in candidates:
        try:
            valid.append((candidate.stat().st_mtime_ns, candidate))
        except OSError:
            continue

    for _, candidate in sorted(valid, reverse=True):
        for attempt in range(4):
            try:
                return json.loads(candidate.read_text(encoding="utf-8-sig"))
            except PermissionError:
                if attempt < 3:
                    time.sleep(0.02 * (attempt + 1))
            except (OSError, ValueError):
                break
    return default


def archive_json(path: str | Path, prefix: str | None = None) -> Path | None:
    """Move a state file and its recovery sidecars to one readable audit bundle.

    Call only after its writer has stopped. A locked main file fails before any
    sidecar moves, so a caller can leave task state unchanged and retry later.
    """
    path = Path(path)
    archived = path.with_name(f"{prefix or path.stem}-{time.time_ns()}{path.suffix}")
    found = False
    if path.exists():
        path.rename(archived)
        found = True
    for pending in sorted(_pending_jsons(path)):
        pending.rename(
            archived.with_name(archived.name + pending.name[len(path.name) :])
        )
        found = True
    return archived if found else None


def atomic_text(path: str | Path, text: str, *, retries: int = 40) -> Path:
    """Atomically replace a normal text file or fail without corrupting the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".writing-" + uuid.uuid4().hex)
    try:
        with tmp.open("w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(max(1, retries)):
            try:
                os.replace(tmp, path)
                return path
            except PermissionError:
                time.sleep(min(0.2, 0.02 * (attempt + 1)))
        raise PermissionError(
            f"Could not replace locked file {path}. New content was not committed."
        )
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def rotate_file(path: str | Path, max_bytes: int, keep: int = 3) -> bool:
    """Best-effort bounded log rotation. A rotation failure never kills a task."""
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size < max_bytes:
            return True
        for index in range(keep, 0, -1):
            source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
            target = path.with_name(f"{path.name}.{index}")
            if source.exists():
                if index == keep:
                    target.unlink(missing_ok=True)
                os.replace(source, target)
        return True
    except OSError:
        return False


def append_text_line(path: str | Path, line: str, *, max_bytes: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rotate_file(path, max_bytes):
        # A viewer/antivirus may allow appends while denying renames. Drop new
        # diagnostics while locked rather than growing an unbounded file.
        return
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line.rstrip("\n") + "\n")
        stream.flush()


def _merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    out = dict(base)
    for key, value in override.items():
        out[key] = _merge(out[key], value) if key in out else value
    return out


def _bounded_int(value, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{name} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer.") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def _bounded_float(value, minimum: float, maximum: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number.") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def validate_settings(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("config.json must contain a JSON object.")

    cfg = copy.deepcopy(cfg)
    provider = str(cfg.get("provider", "")).strip().lower()
    if provider not in {"lmstudio", "openai-compatible"}:
        raise ValueError("provider must be 'lmstudio' or 'openai-compatible'.")
    cfg["provider"] = provider

    api_base = str(cfg.get("api_base", "")).strip().rstrip("/")
    parsed = urlparse(api_base)
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("api_base contains an invalid port.") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("api_base must be a valid http:// or https:// URL.")
    cfg["api_base"] = api_base

    cfg["context_length"] = _bounded_int(
        cfg.get("context_length"), 2048, 1_048_576, "context_length"
    )
    requested_output = _bounded_int(
        cfg.get("max_output_tokens"),
        128,
        131072,
        "max_output_tokens",
    )
    cfg["max_output_tokens"] = min(
        requested_output,
        max(128, cfg["context_length"] // 2),
    )
    cfg["prediction_timeout_seconds"] = _bounded_int(
        cfg.get("prediction_timeout_seconds"), 30, 7200, "prediction_timeout_seconds"
    )
    cfg["stream_read_timeout_seconds"] = _bounded_int(
        cfg.get("stream_read_timeout_seconds"), 30, 7200, "stream_read_timeout_seconds"
    )
    cfg["tool_timeout_seconds"] = _bounded_int(
        cfg.get("tool_timeout_seconds"), 30, 86400, "tool_timeout_seconds"
    )
    cfg["supervisor_inference_stale_seconds"] = _bounded_int(
        cfg.get("supervisor_inference_stale_seconds"),
        60,
        86400,
        "supervisor_inference_stale_seconds",
    )
    cfg["supervisor_tool_stale_seconds"] = _bounded_int(
        cfg.get("supervisor_tool_stale_seconds"),
        60,
        172800,
        "supervisor_tool_stale_seconds",
    )
    cfg["ui_port"] = _bounded_int(cfg.get("ui_port"), 1024, 65535, "ui_port")
    cfg["blender_port"] = _bounded_int(
        cfg.get("blender_port"), 1024, 65535, "blender_port"
    )
    cfg["max_download_mb"] = _bounded_int(
        cfg.get("max_download_mb"), 1, 65536, "max_download_mb"
    )
    cfg["event_log_max_mb"] = _bounded_int(
        cfg.get("event_log_max_mb"), 1, 1024, "event_log_max_mb"
    )
    cfg["raw_log_max_mb"] = _bounded_int(
        cfg.get("raw_log_max_mb"), 1, 4096, "raw_log_max_mb"
    )
    cfg["backups_per_file"] = _bounded_int(
        cfg.get("backups_per_file"), 0, 200, "backups_per_file"
    )

    cfg["temperature"] = _bounded_float(cfg.get("temperature"), 0.0, 2.0, "temperature")
    cfg["top_p"] = _bounded_float(cfg.get("top_p"), 0.0, 1.0, "top_p")
    cfg["top_k"] = _bounded_int(cfg.get("top_k"), 0, 1000, "top_k")
    cfg["lmstudio_gpu_fraction"] = _bounded_float(
        cfg.get("lmstudio_gpu_fraction"), 0.0, 1.0, "lmstudio_gpu_fraction"
    )

    for name in (
        "auto_model_recovery",
        "lmstudio_speculative_mtp",
        "vision",
    ):
        if not isinstance(cfg.get(name), bool):
            raise ValueError(f"{name} must be true or false.")

    capabilities = cfg.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ValueError("capabilities must be an object.")
    known_capabilities = {"web", "desktop", "media", "blender", "extra"}
    normalized_caps = {}
    for name in known_capabilities:
        value = capabilities.get(name, DEFAULT_SETTINGS["capabilities"][name])
        if not isinstance(value, bool):
            raise ValueError(f"capabilities.{name} must be true or false.")
        normalized_caps[name] = value
    cfg["capabilities"] = normalized_caps

    # Migration from the older duplicate Blender enable switch.
    if "blender_enabled" in cfg:
        if not isinstance(cfg["blender_enabled"], bool):
            raise ValueError("Legacy blender_enabled must be true or false.")
        if cfg["blender_enabled"]:
            cfg["capabilities"]["blender"] = True
    cfg.pop("blender_enabled", None)
    cfg.pop("interpreter_exe", None)
    cfg.pop("lm_base", None)

    if not isinstance(cfg.get("request_overrides"), dict):
        raise ValueError("request_overrides must be an object.")
    for reserved in ("model", "messages", "tools", "stream", "n"):
        cfg["request_overrides"].pop(reserved, None)

    servers = cfg.get("extra_mcp_servers")
    if not isinstance(servers, dict):
        raise ValueError("extra_mcp_servers must be an object.")
    for name, spec in servers.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(name)):
            raise ValueError(f"Invalid extra MCP server name: {name!r}")
        if not isinstance(spec, dict) or not str(spec.get("command", "")).strip():
            raise ValueError(f"extra_mcp_servers.{name}.command is required.")
        if not isinstance(spec.get("args", []), list):
            raise ValueError(f"extra_mcp_servers.{name}.args must be a list.")
        if not all(isinstance(arg, str) for arg in spec.get("args", [])):
            raise ValueError(f"extra_mcp_servers.{name}.args must contain strings.")
        if not isinstance(spec.get("env", {}), dict):
            raise ValueError(f"extra_mcp_servers.{name}.env must be an object.")
        if not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in spec.get("env", {}).items()
        ):
            raise ValueError(
                f"extra_mcp_servers.{name}.env must contain string keys/values."
            )

    cfg["preferred_model"] = str(cfg.get("preferred_model") or "").strip()
    cfg["model_load_identifier"] = str(cfg.get("model_load_identifier") or "").strip()
    cfg["browser_channel"] = str(cfg.get("browser_channel") or "chrome").strip()
    cfg["blender_exe"] = str(cfg.get("blender_exe") or "").strip()
    cfg["git_bin"] = str(cfg.get("git_bin") or "").strip()
    cfg["lmstudio_cli"] = str(cfg.get("lmstudio_cli") or "").strip()
    cfg["config_version"] = CONFIG_VERSION
    return cfg


def settings() -> dict:
    missing = object()
    raw = read_json(CONFIG, missing)
    if raw is missing:
        if CONFIG.exists() or _pending_jsons(CONFIG):
            raise ValueError(
                "config.json is unreadable or invalid JSON; repair it before starting."
            )
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("config.json must contain a JSON object.")
    if "lm_base" in raw and "api_base" not in raw:
        raw["api_base"] = raw["lm_base"]
    cfg = _merge(DEFAULT_SETTINGS, raw)
    return validate_settings(cfg)


def ensure_data_layout() -> Path:
    for name in (
        "Tasks",
        "Logs",
        "BrowserProfile",
        "Downloads",
        "Screenshots",
        "Prompts",
        "State",
    ):
        (DATA_ROOT / name).mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists() and not _pending_jsons(CONFIG):
        atomic_json(CONFIG, DEFAULT_SETTINGS)
    # Validate immediately so failures are clear at startup rather than hours into a task.
    settings()
    return DATA_ROOT


def standard_user() -> None:
    if os.name == "nt" and ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError(
            "Run Task Continuity Agent normally, without Run as administrator."
        )


def task_dir() -> Path:
    value = os.environ.get("TASK_CONTINUITY_TASK_DIR")
    return (
        Path(value).resolve()
        if value
        else (DATA_ROOT / "Tasks" / "diagnostics").resolve()
    )


def resolve_path(value: str | Path, base: str | Path | None = None) -> Path:
    """Resolve relative user/model paths against the current task, never process cwd."""
    if any(ord(char) < 32 for char in str(value)):
        raise ValueError("Path contains a newline/control character.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(base or task_dir()) / path
    return path.resolve()


def capability_enabled(name: str) -> bool:
    return bool(settings().get("capabilities", {}).get(name, False))


def blender_rpc(command: str, params=None, timeout: int = 90):
    if not capability_enabled("blender"):
        raise RuntimeError("Blender capability is disabled in config.json.")
    port = int(settings().get("blender_port", 9876))
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(timeout)
        sock.sendall(json.dumps({"type": command, "params": params or {}}).encode())
        data = bytearray()
        while len(data) < 40 * 1024 * 1024:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError(
                    "Blender closed the socket before a complete reply."
                )
            data.extend(chunk)
            try:
                return json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        raise ValueError("Blender response exceeds 40 MB.")


def _blender_version_key(path: Path) -> tuple[int, ...]:
    match = re.search(r"Blender\s+(\d+(?:\.\d+)*)", str(path), re.I)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def find_blender() -> str:
    configured = str(settings().get("blender_exe") or "").strip()
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("blender")
    if discovered:
        return discovered
    if os.name == "nt":
        root = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Blender Foundation"
        )
        try:
            candidates = list(root.glob("Blender */blender.exe"))
            if candidates:
                return str(max(candidates, key=_blender_version_key))
        except OSError:
            pass
    raise RuntimeError(
        "Blender is enabled but no Blender executable was found. "
        "Configure blender_exe or add Blender to PATH."
    )


def _tracked_blender_process():
    info = read_json(DATA_ROOT / "State" / "live-blender.json", {}) or {}
    try:
        process = psutil.Process(int(info["pid"]))
        created = float(info["created"])
        if (
            abs(process.create_time() - created) < 0.1
            and process.is_running()
            and process.name().lower() == "blender.exe"
        ):
            return process
    except (KeyError, TypeError, ValueError, psutil.Error):
        pass
    return None


def _tracked_blender_logger():
    info = read_json(DATA_ROOT / "State" / "live-blender.json", {}) or {}
    try:
        process = psutil.Process(int(info["logger_pid"]))
        if (
            abs(process.create_time() - float(info["logger_created"])) < 0.1
            and process.is_running()
            and str(APP_DIR / "blender_launcher.py").lower()
            in [arg.lower() for arg in process.cmdline()]
        ):
            return process
    except (KeyError, TypeError, ValueError, psutil.Error):
        pass
    return None


def start_blender(checkpoint: str = ""):
    try:
        return blender_rpc("ping", timeout=5)
    except TimeoutError as error:
        raise RuntimeError(
            "The existing Blender bridge is busy. Inspect it before starting another instance."
        ) from error
    except (OSError, ConnectionError):
        pass

    if not capability_enabled("blender"):
        raise RuntimeError("Blender capability is disabled in config.json.")

    tracked = _tracked_blender_process()
    if tracked or _tracked_blender_logger():
        raise RuntimeError(
            "A tracked Blender process is still running but its bridge is unavailable. "
            "Inspect or close that instance instead of starting a duplicate."
        )

    cfg = settings()
    args = [find_blender()]
    if checkpoint:
        project = resolve_path(checkpoint)
        if not project.is_file() or project.suffix.lower() != ".blend":
            raise ValueError("Recovery checkpoint must be an existing .blend file.")
        args.append(str(project))
    else:
        args.append("--factory-startup")

    env = dict(os.environ)
    env["TASK_CONTINUITY_DATA"] = str(DATA_ROOT)
    env["TASK_CONTINUITY_BLENDER_PORT"] = str(cfg.get("blender_port", 9876))
    args += ["--python", str(APP_DIR / "blender_bootstrap.py")]

    log = DATA_ROOT / "Logs" / "blender-startup.log"
    ledger = DATA_ROOT / "State" / "live-blender.json"
    launch_id = uuid.uuid4().hex
    try:
        logger = subprocess.Popen(
            [
                str(PYTHON),
                str(APP_DIR / "blender_launcher.py"),
                str(ledger),
                str(int(cfg["raw_log_max_mb"]) * 1024 * 1024),
                json.dumps(args),
                launch_id,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=NO_WINDOW | (0x01000000 if os.name == "nt" else 0),
            start_new_session=os.name != "nt",
            close_fds=True,
        )
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 5:
            raise RuntimeError(
                "Windows denied independent Blender logger launch; no coupled fallback was launched."
            ) from error
        raise
    import threading

    threading.Thread(target=logger.wait, daemon=True).start()

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        info = read_json(ledger, {}) or {}
        if info.get("launch_id") == launch_id and info.get("error"):
            raise RuntimeError("Blender startup failed: " + info["error"])
        if logger.poll() is not None:
            raise RuntimeError(f"Blender exited during bridge startup. See {log}.")
        time.sleep(1)
        try:
            return blender_rpc("ping", timeout=3)
        except (OSError, ConnectionError):
            continue

    # Stop only the instance identified by this exact launch receipt.
    info = read_json(ledger, {}) or {}
    if info.get("launch_id") == launch_id:
        processes = []
        logger_process = _tracked_blender_logger()
        if logger_process:
            try:
                processes.extend(reversed(logger_process.children(recursive=True)))
            except psutil.Error:
                pass
        child = _tracked_blender_process()
        if child and child.pid not in {process.pid for process in processes}:
            processes.append(child)
        if logger_process:
            processes.append(logger_process)
        for process in processes:
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=10)
                except psutil.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                except psutil.Error:
                    pass
    raise RuntimeError(
        f"Blender started but port {cfg.get('blender_port', 9876)} did not respond. "
        f"The failed startup instance was stopped. See {log}."
    )
