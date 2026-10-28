"""Regression tests for resource limits, runtime isolation, and request failures."""

import asyncio
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import actor
import agent
import common
import tools
import work_tools
import job_runner


class FileIsolation(unittest.TestCase):
    def test_scene_verification_does_not_inherit_an_open_tool_input_pipe(self):
        # Keep the parent input pipe open, as MCP does. The helper cannot finish
        # until it sees EOF; a verifier that inherits that pipe will time out.
        worker = r"""
import json, subprocess, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(sys.argv[1]) / 'app'))
import acceptance
folder = Path(sys.argv[2])
(folder / 'scene.blend').write_bytes(b'BLENDER test fixture')
(folder / 'acceptance.json').write_text(json.dumps({'required_extensions':['.blend'],'visual_review_required':False,'durable_deliverable_required':True}))
real_run = subprocess.run
helper = "import sys,types;sys.stdin.buffer.read();sys.modules['bpy']=types.SimpleNamespace(context=types.SimpleNamespace(scene=types.SimpleNamespace(objects=[1],camera=True)));exec(sys.argv[1])"
def verify(args, **kwargs):
    return real_run([sys.executable, '-c', helper, args[-1]], stdin=kwargs.get('stdin'), capture_output=True, text=True, timeout=2)
with patch.object(acceptance, 'find_blender', return_value='fixture'), patch.object(acceptance.subprocess, 'run', side_effect=verify):
    acceptance.validate_completion(folder, {'status':'completed','summary':'scene','verification':'checked','files':[{'path':'scene.blend'}]})
print('VERIFIED_WITH_OPEN_PARENT_INPUT')
"""
        with tempfile.TemporaryDirectory() as td:
            process = subprocess.Popen(
                [sys.executable, "-c", worker, str(ROOT), td],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                process.wait(timeout=10)
                process.stdin.close()
                process.stdin = None
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, (out + err).decode())
                self.assertIn(b"VERIFIED_WITH_OPEN_PARENT_INPUT", out)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    @unittest.skipUnless(os.name == "nt", "Windows watchdog")
    def test_watchdog_survives_locked_diagnostic_log_and_recovers(self):
        script = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:WATCH_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Watchdog parse failed.' }
$function = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Write-WatchdogEvent'}, $true)
Invoke-Expression $function.Extent.Text
$Logs = $env:WATCH_LOGS
$log = Join-Path $Logs 'watchdog.log'
[IO.File]::WriteAllBytes($log, (New-Object byte[] (1MB)))
$locked = [IO.File]::Open($log, 'Open', 'Read', 'Read')
try {
    1..5 | ForEach-Object { Write-WatchdogEvent 'locked rotation' $_ }
    if ((Get-Item -LiteralPath $log).Length -ne 1MB) { throw 'Locked log grew.' }
} finally { $locked.Dispose() }
Write-WatchdogEvent 'recovered' 6
if (!(Test-Path -LiteralPath ($log + '.previous'))) { throw 'Rotation did not recover.' }
if ((Get-Content -LiteralPath $log -Raw) -notmatch 'recovered; retry 6') { throw 'Logging did not recover.' }
$locked = [IO.File]::Open($log, 'Open', 'Read', 'Read')
try { Write-WatchdogEvent 'locked append' 7 } finally { $locked.Dispose() }
Write-WatchdogEvent 'still alive' 8
if ((Get-Content -LiteralPath $log -Raw) -notmatch 'still alive') { throw 'Append lock stopped recovery.' }
"""
        with tempfile.TemporaryDirectory() as td:
            checked = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", script],
                env=dict(
                    os.environ,
                    WATCH_SOURCE=str(ROOT / "watch-agent.ps1"),
                    WATCH_LOGS=str(Path(td).resolve()),
                ),
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_locked_rotation_does_not_grow_supervisor_log(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.log"
            path.write_bytes(b"x" * 32)
            with patch.object(
                common.os, "replace", side_effect=PermissionError("locked")
            ):
                for _ in range(10):
                    common.append_text_line(path, "new event", max_bytes=32)
            self.assertEqual(path.stat().st_size, 32)
            common.append_text_line(path, "logging recovered", max_bytes=32)
            self.assertIn("logging recovered", path.read_text())

    def test_locked_job_log_still_drains_pipe_without_growing(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stdout.log"
            path.write_bytes(b"x" * 32)
            stream = io.BufferedReader(io.BytesIO(b"y" * (1024 * 1024)))
            errors = []
            with patch.object(
                common.os, "replace", side_effect=PermissionError("locked")
            ):
                job_runner._copy_log(stream, path, 32, errors)
            self.assertFalse(errors)
            self.assertTrue(stream.closed)
            self.assertEqual(path.stat().st_size, 32)

    def test_archiving_includes_pending_only_completion(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "result.json"
            pending = path.with_name(path.name + ".pending-123-test.json")
            pending.write_text('{"summary":"latest completion"}')
            archived = common.archive_json(path)
            self.assertEqual(common.read_json(archived)["summary"], "latest completion")
            self.assertIsNone(common.read_json(path))
            self.assertFalse(pending.exists())

    def test_locked_main_leaves_pending_completion_in_place(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "result.json"
            path.write_text("{}")
            pending = path.with_name(path.name + ".pending-123-test.json")
            pending.write_text('{"summary":"latest completion"}')
            with patch.object(Path, "rename", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    common.archive_json(path)
            self.assertTrue(pending.exists())
            self.assertTrue(path.exists())

    def test_shared_runtime_and_other_tasks_are_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            data = root / "data"
            task = data / "Tasks/current"
            task.mkdir(parents=True)
            with (
                patch.object(work_tools, "ROOT", root),
                patch.object(work_tools, "DATA_ROOT", data),
                patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": str(task)}),
            ):
                for path in (
                    data / "config.json",
                    data / "State/server.json",
                    data / "Tasks/another/result.txt",
                    root / "app/actor.py",
                ):
                    with self.subTest(path=path), self.assertRaises(ValueError):
                        work_tools.writable(path)
                self.assertEqual(
                    work_tools.writable(task / "result.txt"), task / "result.txt"
                )
                self.assertEqual(
                    work_tools.writable(root.parent / "user-project.txt"),
                    root.parent / "user-project.txt",
                )

    def test_backup_retention_handles_brackets_in_filename(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            path = Path(td) / "report[1].txt"
            with patch.object(
                work_tools, "settings", return_value={"backups_per_file": 2}
            ), patch.object(work_tools.time, "time_ns", side_effect=range(8, 13)):
                for value in range(5):
                    path.write_text(str(value), encoding="utf-8")
                    # Copies inherit tied or older source mtimes. Retention must
                    # follow backup creation order, including numeric 9 -> 10.
                    source_mtime = (5, 5, 4, 3, 2)[value] * 1_000_000_000
                    os.utime(path, ns=(source_mtime, source_mtime))
                    work_tools._backup_existing(path)
            backups = list((Path(td) / "Backups").iterdir())
            self.assertEqual(len(backups), 2)
            self.assertEqual({p.read_text() for p in backups}, {"3", "4"})


class BoundedStreams(unittest.TestCase):
    def test_prediction_uses_native_reasoning_and_keeps_explicit_override(self):
        for overrides, expected in (
            ({}, "none"),
            ({"reasoning_effort": "high"}, "high"),
        ):
            with tempfile.TemporaryDirectory() as td:
                instance = actor.Agent.__new__(actor.Agent)
                instance.folder = Path(td)
                instance.cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
                instance.cfg["request_overrides"] = overrides
                instance.model = "test"
                instance.contract = {"requires_action": False}
                instance.state = {"step": 1}
                instance.persist = instance.compact = Mock()
                instance.active_tools = Mock(return_value=[])
                instance.messages = Mock(return_value=[])
                response = Mock(ok=True)
                response.iter_content.return_value = [
                    b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n'
                ]
                context = Mock()
                context.__enter__ = Mock(return_value=response)
                context.__exit__ = Mock(return_value=False)
                with (
                    patch.dict(
                        os.environ,
                        {"TASK_CONTINUITY_REASONING_OPTIONS": '["on","off"]'},
                    ),
                    patch.object(actor.requests, "post", return_value=context) as post,
                    patch.object(actor, "emit"),
                ):
                    instance.predict([])
                self.assertEqual(
                    post.call_args.kwargs["json"]["reasoning_effort"], expected
                )

    def test_model_discovery_preserves_native_reasoning_options(self):
        response = Mock()
        response.json.return_value = {
            "models": [
                {
                    "type": "llm",
                    "key": "test-model",
                    "loaded_instances": [
                        {"id": "test-instance", "config": {"context_length": 16384}}
                    ],
                    "capabilities": {"reasoning": {"allowed_options": ["on", "off"]}},
                }
            ]
        }
        with (
            patch.object(
                agent, "settings", return_value=copy.deepcopy(common.DEFAULT_SETTINGS)
            ),
            patch.object(agent.requests, "get", return_value=response),
        ):
            self.assertEqual(agent.model_info()["reasoning_options"], ["on", "off"])

    def test_reasoning_uses_advertised_binary_or_tiered_options(self):
        for options, expected in (
            (["on", "off"], ("low", "none")),
            (["low", "medium", "high"], ("low", "low")),
            (["on"], ("low", "low")),
            ([], (None, None)),
        ):
            with (
                self.subTest(options=options),
                patch.dict(
                    os.environ,
                    {"TASK_CONTINUITY_REASONING_OPTIONS": json.dumps(options)},
                ),
            ):
                self.assertEqual(
                    (actor.reasoning_setting(True), actor.reasoning_setting(False)),
                    expected,
                )

    def test_invalid_reasoning_metadata_does_not_invent_a_setting(self):
        for value in ("not json", '{"off":true}', "[null]"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"TASK_CONTINUITY_REASONING_OPTIONS": value}),
            ):
                self.assertIsNone(actor.reasoning_setting(False))

    def test_fragmented_lines_and_final_line_are_preserved(self):
        response = Mock()
        response.iter_content.return_value = [
            b"data: one\r",
            b"\n\ndata",
            b": two\n",
            b"data: [DONE]",
        ]
        self.assertEqual(
            list(actor.stream_lines(response)),
            [b"data: one", b"", b"data: two", b"data: [DONE]"],
        )

    def test_newline_free_stream_stops_at_byte_limit(self):
        consumed = []

        def chunks():
            for _ in range(100):
                consumed.append(True)
                yield b"x" * 16

        response = Mock()
        response.iter_content.return_value = chunks()
        with self.assertRaisesRegex(RuntimeError, "byte limit"):
            list(actor.stream_lines(response, maximum=32))
        self.assertEqual(len(consumed), 3)

    def test_token_limited_tool_call_is_discarded(self):
        with tempfile.TemporaryDirectory() as td:
            instance = actor.Agent.__new__(actor.Agent)
            instance.folder = Path(td)
            instance.cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
            instance.model = "test"
            instance.contract = {"requires_action": False}
            instance.state = {"step": 0}
            instance.persist = instance.compact = Mock()
            instance.active_tools = Mock(return_value=[])
            instance.messages = Mock(return_value=[])
            chunk = {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {
                                        "name": "write_text",
                                        "arguments": "{}",
                                    },
                                }
                            ]
                        },
                        "finish_reason": "length",
                    }
                ]
            }
            response = Mock(ok=True)
            response.iter_content.return_value = [
                b"data: " + json.dumps(chunk).encode() + b"\n"
            ]
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            with (
                patch.object(actor.requests, "post", return_value=context),
                patch.object(actor, "emit"),
            ):
                with self.assertRaisesRegex(RuntimeError, "token limit"):
                    instance.predict([])


class RequestValidation(unittest.TestCase):
    def handler(self, path, data):
        handler = agent.Handler.__new__(agent.Handler)
        body = json.dumps(data).encode()
        handler.rfile = io.BytesIO(body)
        handler.headers = {"Content-Length": str(len(body))}
        handler.path = path
        handler.send = Mock()
        return handler

    def test_nonfinite_or_invalid_time_budget_is_rejected(self):
        for value in (float("nan"), float("inf"), -1, 0, 73, "bad", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                agent.task_hours(value)
        self.assertEqual(agent.task_hours("0.5"), 0.5)

    def test_nonobject_body_is_rejected_before_task_lookup(self):
        handler = self.handler("/api/resume", [])
        with patch.object(agent, "find_task") as lookup:
            handler.post_action()
        lookup.assert_not_called()
        self.assertEqual(handler.send.call_args.args[0], 400)

    def test_invalid_followup_does_not_mutate_task(self):
        handler = self.handler(
            "/api/followup", {"id": "test", "text": "change output", "hours": "bad"}
        )
        with (
            patch.object(agent, "find_task") as lookup,
            patch.object(agent, "atomic_json") as write,
        ):
            handler.post_action()
        lookup.assert_not_called()
        write.assert_not_called()
        self.assertEqual(handler.send.call_args.args[0], 400)

    def test_locked_result_does_not_partially_queue_resume(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            (task / "result.json").write_text("{}")
            handler = self.handler("/api/resume", {"id": "test", "hours": 1})
            with (
                patch.object(agent, "find_task", return_value=task),
                patch.object(agent, "ACTIVE", None),
                patch.object(agent, "save_meta") as save,
                patch.object(agent, "launch_task") as launch,
                patch.object(Path, "rename", side_effect=PermissionError("locked")),
            ):
                handler.post_action()
            save.assert_not_called()
            launch.assert_not_called()
            self.assertEqual(handler.send.call_args.args[0], 400)


class BrowserDownloads(unittest.IsolatedAsyncioTestCase):
    def download(self, save):
        return Mock(
            suggested_filename="CON.txt",
            save_as=AsyncMock(side_effect=save),
            cancel=AsyncMock(),
            delete=AsyncMock(),
        )

    async def test_success_uses_unique_name_and_hash(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):

            async def save(path):
                Path(path).write_bytes(b"verified download")

            download = self.download(save)
            result = json.loads(await tools.save_browser_download(download))
            self.assertEqual(
                Path(result["download"]).read_bytes(), b"verified download"
            )
            self.assertEqual(result["sha256"], common.file_sha256(result["download"]))
            self.assertFalse(Path(result["download"]).name.startswith("CON"))
            download.cancel.assert_not_awaited()
            download.delete.assert_awaited_once()

    async def test_failed_download_preserves_existing_and_removes_partial(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            original = Path(td) / "Downloads/report.txt"
            original.parent.mkdir()
            original.write_bytes(b"original")

            async def save(path):
                Path(path).write_bytes(b"partial")
                raise ConnectionError("transfer failed")

            download = self.download(save)
            download.suggested_filename = original.name
            with self.assertRaises(ConnectionError):
                await tools.save_browser_download(download)
            self.assertEqual(list(original.parent.iterdir()), [original])
            self.assertEqual(original.read_bytes(), b"original")
            download.cancel.assert_awaited_once()

    async def test_oversized_browser_scratch_is_cancelled_before_copy(self):
        with (
            tempfile.TemporaryDirectory() as td,
            tempfile.TemporaryDirectory() as scratch,
        ):

            async def save(path):
                (Path(scratch) / "chromium-partial").write_bytes(
                    b"x" * (1024 * 1024 + 1)
                )
                await asyncio.sleep(30)

            download = self.download(save)
            cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
            cfg["max_download_mb"] = 1
            with (
                patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
                patch.object(tools, "settings", return_value=cfg),
                patch.object(
                    tools, "browser_download_dir", SimpleNamespace(name=scratch)
                ),
            ):
                started = time.monotonic()
                with self.assertRaisesRegex(ValueError, "max_download_mb"):
                    await tools.save_browser_download(download)
                self.assertLess(time.monotonic() - started, 3)
            download.cancel.assert_awaited_once()
            self.assertEqual(list((Path(td) / "Downloads").iterdir()), [])

    async def test_browser_launch_failure_cleans_runtime_and_scratch(self):
        runtime = Mock(stop=AsyncMock())
        runtime.chromium.launch_persistent_context = AsyncMock(
            side_effect=RuntimeError("launch failed")
        )
        manager = Mock(start=AsyncMock(return_value=runtime))
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(tools, "DATA_ROOT", Path(td)),
            patch.object(tools, "browser_context", None),
            patch.object(tools, "playwright_runtime", None),
            patch.object(tools, "browser_download_dir", None),
            patch("playwright.async_api.async_playwright", return_value=manager),
        ):
            with self.assertRaisesRegex(RuntimeError, "launch failed"):
                await tools.get_browser()
            self.assertIsNone(tools.playwright_runtime)
            self.assertIsNone(tools.browser_download_dir)
            self.assertEqual(list((Path(td) / "BrowserDownloads").iterdir()), [])
        runtime.stop.assert_awaited_once()

    async def test_invalid_tab_and_new_url_do_not_act(self):
        context = Mock(pages=[Mock()], new_page=AsyncMock())
        with patch.object(tools, "get_browser", AsyncMock(return_value=context)):
            for action, kwargs in (
                ("snapshot", {"tab": -1}),
                ("snapshot", {"tab": 2}),
                ("new", {"value": "javascript:alert(1)"}),
            ):
                with (
                    self.subTest(action=action, kwargs=kwargs),
                    self.assertRaises(ValueError),
                ):
                    await tools.browser(action, **kwargs)
        context.new_page.assert_not_awaited()


class ControlTransitions(unittest.TestCase):
    def setup_task(self, folder, result=False):
        common.atomic_json(
            folder / "task.json",
            {
                "id": "test",
                "objective": "What is two plus two?",
                "created": time.time(),
                "deadline": time.time() + 60,
                "status": "running",
            },
        )
        if result:
            common.atomic_json(
                folder / "result.json",
                {
                    "status": "completed",
                    "summary": "old answer",
                    "verification": "checked",
                    "files": [],
                },
            )

    def globals(self, task, stop, wake, steer):
        return patch.multiple(
            agent, ACTIVE=task, PROC=None, STOP=stop, WAKE=wake, STEER=steer
        )

    def test_pause_during_model_discovery_prevents_spawn(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            self.setup_task(task)
            stop, wake, steer = threading.Event(), threading.Event(), threading.Event()
            wake.set()

            def model():
                wake.clear()
                return {"model": "test", "context": 4096}

            def paused(_):
                stop.set()
                return False

            with (
                self.globals(task, stop, wake, steer),
                patch.object(agent, "keep_awake"),
                patch.object(agent, "model_info", side_effect=model),
                patch.object(wake, "wait", side_effect=paused),
                patch.object(agent.subprocess, "Popen") as spawn,
            ):
                agent.worker(task)
            spawn.assert_not_called()

    def test_pause_during_completion_verification_is_honored(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            self.setup_task(task, result=True)
            stop, wake, steer = threading.Event(), threading.Event(), threading.Event()
            wake.set()

            def paused(_):
                stop.set()
                return False

            with (
                self.globals(task, stop, wake, steer),
                patch.object(agent, "keep_awake"),
                patch.object(
                    agent, "validate_completion", side_effect=lambda *_: wake.clear()
                ),
                patch.object(wake, "wait", side_effect=paused),
                patch.object(agent, "model_info") as discover,
            ):
                agent.worker(task)
            discover.assert_not_called()
            self.assertEqual(common.read_json(task / "task.json")["status"], "stopped")
            self.assertTrue((task / "result.json").exists())

    def test_followup_during_verification_cannot_accept_old_result(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            self.setup_task(task, result=True)
            stop, wake, steer = threading.Event(), threading.Event(), threading.Event()
            wake.set()
            handler = RequestValidation().handler(
                "/api/followup", {"id": "test", "text": "give a new answer", "hours": 1}
            )
            validations = []

            def verify(folder, report):
                validations.append(report["summary"])
                if len(validations) == 1:
                    handler.post_action()

            def spawn(*args, **kwargs):
                common.atomic_json(
                    task / "result.json",
                    {
                        "status": "completed",
                        "summary": "new answer",
                        "verification": "new request checked",
                        "files": [],
                    },
                )
                return Mock(
                    pid=999999999,
                    stdout=io.StringIO(""),
                    stderr=io.StringIO(""),
                    wait=Mock(return_value=0),
                )

            with (
                self.globals(task, stop, wake, steer),
                patch.object(agent, "keep_awake"),
                patch.object(agent, "find_task", return_value=task),
                patch.object(agent, "validate_completion", side_effect=verify),
                patch.object(
                    agent, "model_info", return_value={"model": "test", "context": 4096}
                ),
                patch.object(agent.subprocess, "Popen", side_effect=spawn) as launched,
            ):
                agent.worker(task)
            launched.assert_called_once()
            self.assertEqual(handler.send.call_args.args[0], 200)
            self.assertEqual(
                common.read_json(task / "result.json")["summary"], "new answer"
            )
            self.assertEqual(
                common.read_json(task / "steering.json"), ["give a new answer"]
            )
            self.assertEqual(
                common.read_json(task / "task.json")["status"], "completed"
            )

    def test_followup_after_acceptance_is_queued_before_slot_release(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            self.setup_task(task, result=True)
            stop, wake, steer = threading.Event(), threading.Event(), threading.Event()
            wake.set()
            handler = RequestValidation().handler(
                "/api/followup",
                {"id": "test", "text": "continue with new work", "hours": 1},
            )

            def awake(enabled):
                if not enabled:
                    handler.post_action()

            with (
                self.globals(task, stop, wake, steer),
                patch.object(agent, "keep_awake", side_effect=awake),
                patch.object(agent, "find_task", return_value=task),
                patch.object(agent, "launch_task") as launch,
            ):
                agent.worker(task)
            launch.assert_called_once_with(task)
            self.assertEqual(common.read_json(task / "task.json")["status"], "queued")
            self.assertIsNone(common.read_json(task / "result.json"))


if __name__ == "__main__":
    unittest.main()
