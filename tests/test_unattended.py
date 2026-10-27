"""Regression coverage for interrupted and unattended runs; no local model required."""

import asyncio
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import acceptance
import actor
import agent
import common
import job_runner
import production
import runtime_policy
import tools
import work_tools


class ConfigAndPersistence(unittest.TestCase):
    def test_persistent_lock_keeps_bounded_pending_states(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            common.atomic_json(path, {"v": 0})
            replace = os.replace

            def locked(source, target):
                if Path(target) == path:
                    raise PermissionError("locked")
                return replace(source, target)

            with (
                patch.object(common.os, "replace", side_effect=locked),
                patch.object(common.time, "sleep"),
            ):
                for i in range(1, 10):
                    self.assertFalse(common.atomic_json(path, {"v": i}))
            self.assertLessEqual(len(common._pending_jsons(path)), 3)
            self.assertEqual(common.read_json(path), {"v": 9})

    def test_invalid_url_port_and_noninteger_values_are_rejected(self):
        for override in (
            {"api_base": "http://127.0.0.1:bad/v1"},
            {"top_k": 1.5},
            {"top_k": True},
            {"temperature": False},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                common.validate_settings(
                    {**copy.deepcopy(common.DEFAULT_SETTINGS), **override}
                )

    def test_large_text_append_is_rejected_without_changing_file(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            path = Path(td) / "large.txt"
            with path.open("wb") as output:
                output.truncate(33 * 1024 * 1024)
            with self.assertRaisesRegex(ValueError, "too large"):
                work_tools.write_text(str(path), "suffix", append=True)
            self.assertEqual(path.stat().st_size, 33 * 1024 * 1024)

    def test_task_state_is_protected_but_external_task_json_is_editable(self):
        with (
            tempfile.TemporaryDirectory() as td,
            tempfile.TemporaryDirectory() as external,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            for name in (
                "task.json",
                "progress.json",
                "quality-review.json",
                "Jobs/demo/job.json",
            ):
                with self.assertRaises(ValueError):
                    work_tools.writable(name)
            path = Path(external) / "task.json"
            work_tools.write_text(str(path), "{}")
            self.assertEqual(path.read_text(), "{}")

    def test_corrupt_or_nonobject_config_never_silently_uses_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            for value in ("{broken", "[]", "null", "true"):
                path.write_text(value)
                with (
                    patch.object(common, "CONFIG", path),
                    self.assertRaises(ValueError),
                ):
                    common.settings()

    def test_validation_does_not_mutate_input_or_defaults(self):
        cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
        cfg["request_overrides"] = {"stream": False, "custom": True}
        before = copy.deepcopy(cfg)
        checked = common.validate_settings(cfg)
        self.assertEqual(cfg, before)
        self.assertNotIn("stream", checked["request_overrides"])
        self.assertTrue(checked["request_overrides"]["custom"])

    def test_invalid_mcp_arguments_and_boolean_are_rejected(self):
        for override in (
            {"vision": "false"},
            {"blender_enabled": "false"},
            {"extra_mcp_servers": {"x": {"command": "x", "args": [1]}}},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                common.validate_settings(
                    {**copy.deepcopy(common.DEFAULT_SETTINGS), **override}
                )

    def test_pending_only_config_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            path = folder / "config.json"
            pending = folder / "config.json.pending-1-a.json"
            pending.write_text(json.dumps({"ui_port": 19876}))
            with (
                patch.object(common, "CONFIG", path),
                patch.object(common, "DATA_ROOT", folder),
            ):
                common.ensure_data_layout()
                self.assertEqual(common.settings()["ui_port"], 19876)
            self.assertFalse(path.exists())

    def test_failed_atomic_text_preserves_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "file.txt"
            path.write_text("original")
            with (
                patch.object(common.os, "replace", side_effect=PermissionError),
                patch.object(common.time, "sleep"),
            ):
                with self.assertRaises(PermissionError):
                    common.atomic_text(path, "replacement", retries=2)
            self.assertEqual(path.read_text(), "original")
            self.assertEqual([p.name for p in path.parent.iterdir()], ["file.txt"])

    def test_corrupt_newest_json_falls_back_to_valid_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            common.atomic_json(path, {"v": 1})
            (path.parent / "state.json.pending-9-x.json").write_text("{broken")
            self.assertEqual(common.read_json(path), {"v": 1})

    def test_log_copy_bounds_newline_free_output(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stdout.log"
            errors = []
            job_runner._copy_log(
                io.BufferedReader(io.BytesIO(b"x" * (2 * 1024 * 1024))),
                path,
                131072,
                errors,
            )
            self.assertFalse(errors)
            self.assertLessEqual(
                sum(p.stat().st_size for p in path.parent.iterdir()),
                3 * (131072 + 65536),
            )
            self.assertEqual(common.tail_bytes(path, 10), b"x" * 10)


class Completion(unittest.TestCase):
    def test_question_with_filename_does_not_require_an_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            contract = acceptance.initialize_contract(task, "Explain what app.py does.")
            self.assertFalse(contract["requires_action"])
            acceptance.validate_completion(
                task,
                {
                    "status": "completed",
                    "summary": "Explained",
                    "verification": "Read source",
                    "files": [],
                },
            )

    def test_action_cannot_complete_with_empty_deliverables(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Build an application")
            with self.assertRaisesRegex(ValueError, "durable deliverable"):
                acceptance.validate_completion(
                    task,
                    {
                        "status": "completed",
                        "summary": "done",
                        "verification": "checked",
                        "files": [],
                    },
                )

    def test_relative_deliverables_and_changed_digest(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Create report.txt")
            out = task / "report.txt"
            out.write_text("first")
            report = {
                "status": "completed",
                "summary": "done",
                "verification": "read back",
                "files": [{"path": "report.txt", "sha256": common.file_sha256(out)}],
            }
            acceptance.validate_completion(task, report)
            out.write_text("second")
            with self.assertRaisesRegex(ValueError, "changed after verification"):
                acceptance.validate_completion(task, report)

    def test_visual_completion_requires_real_review_images(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Create preview.png")
            (task / "preview.png").write_bytes(b"not a picture")
            common.atomic_json(
                task / "quality-review.json",
                {"passed": True, "images": [], "criteria": [{}, {}, {}]},
            )
            with self.assertRaisesRegex(ValueError, "current previews"):
                acceptance.validate_completion(
                    task,
                    {
                        "status": "completed",
                        "summary": "done",
                        "verification": "checked",
                        "files": [{"path": "preview.png"}],
                    },
                )

    def test_unknown_completion_status_rejected(self):
        with self.assertRaises(ValueError):
            acceptance.validate_completion(Path("."), {"status": "running"})


class Streaming(unittest.TestCase):
    def make_agent(self, folder):
        instance = actor.Agent.__new__(actor.Agent)
        instance.folder = folder
        instance.cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
        instance.cfg["provider"] = "openai-compatible"
        instance.model = "test"
        instance.contract = {"requires_action": False}
        instance.state = {"step": 0}
        instance.persist = Mock()
        instance.compact = Mock()
        instance.active_tools = Mock(return_value=[])
        instance.messages = Mock(return_value=[{"role": "user", "content": "test"}])
        return instance

    def predict(self, instance, chunks):
        response = Mock()
        response.ok = True
        response.iter_content.return_value = iter(
            [
                (
                    b"data: " + json.dumps(chunk).encode()
                    if isinstance(chunk, dict)
                    else chunk
                )
                + b"\n"
                for chunk in chunks
            ]
        )
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with (
            patch.object(actor.requests, "post", return_value=context),
            patch.object(actor, "emit"),
        ):
            return instance.predict([])

    def test_fragmented_tool_arguments_are_assembled(self):
        with tempfile.TemporaryDirectory() as td:
            instance = self.make_agent(Path(td))
            reply, finish = self.predict(
                instance,
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "c1",
                                            "function": {
                                                "name": "write_text",
                                                "arguments": '{"path":"',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {
                                                "arguments": 'out.txt","text":"ok"}'
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    },
                    b"data: [DONE]",
                ],
            )
            self.assertEqual(finish, "tool_calls")
            self.assertEqual(
                json.loads(reply["tool_calls"][0]["function"]["arguments"])["text"],
                "ok",
            )

    def test_incomplete_stream_is_discarded(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "finish reason"):
                self.predict(
                    self.make_agent(Path(td)),
                    [{"choices": [{"delta": {"content": "partial"}}]}],
                )

    def test_valid_finish_without_done_is_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            instance = self.make_agent(Path(td))
            reply, _ = self.predict(
                instance,
                [
                    {
                        "choices": [
                            {"delta": {"content": "answer"}, "finish_reason": "stop"}
                        ]
                    }
                ],
            )
            self.assertEqual(reply["content"], "answer")
            self.assertTrue(instance.state["stream_missing_done_sentinel"])

    def test_duplicate_call_ids_are_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as td:
            chunk = {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": i,
                                    "id": "same",
                                    "function": {
                                        "name": "write_text",
                                        "arguments": "{}",
                                    },
                                }
                                for i in (0, 1)
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
            with self.assertRaisesRegex(RuntimeError, "identity"):
                self.predict(self.make_agent(Path(td)), [chunk, b"data: [DONE]"])

    def test_disabled_capability_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as td:
            instance = self.make_agent(Path(td))
            common.atomic_json(
                instance.folder / "task.json", {"deadline": time.time() + 60}
            )
            call = {
                "id": "x",
                "function": {"name": "use_tools", "arguments": '{"group":"blender"}'},
            }
            with self.assertRaisesRegex(ValueError, "disabled"):
                asyncio.run(instance.execute(call))

    def test_hallucinated_tool_outside_active_group_never_executes(self):
        with tempfile.TemporaryDirectory() as td:
            instance = self.make_agent(Path(td))
            common.atomic_json(
                instance.folder / "task.json", {"deadline": time.time() + 60}
            )
            instance.tool_map = {"blender_project": (AsyncMock(), Mock())}
            call = {
                "id": "x",
                "function": {"name": "blender_project", "arguments": "{}"},
            }
            with self.assertRaisesRegex(ValueError, "active capability"):
                asyncio.run(instance.execute(call))
            instance.tool_map["blender_project"][0].call_tool.assert_not_called()

    def test_uncertain_tool_turn_is_recovered_without_replay(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            contract = acceptance.initialize_contract(folder, "Create out.txt")
            common.atomic_json(folder / "task.json", {"objective": "Create out.txt"})
            common.atomic_json(
                folder / "agent-state.json",
                {
                    "objective_id": contract["objective_id"],
                    "turns": [],
                    "step": 0,
                    "inflight_turn": {
                        "reply": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "x",
                                    "function": {
                                        "name": "write_text",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "results": {},
                    },
                },
            )
            instance = actor.Agent(folder, "test")
            self.assertIn("may have executed", instance.turns[-1][1]["content"])
            self.assertNotIn("inflight_turn", instance.state)


class DownloadsAndMedia(unittest.TestCase):
    def test_failed_download_does_not_remove_existing_file(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            dest = Path(td) / "asset.txt"
            dest.write_text("original")
            response = Mock()
            response.headers = {}
            response.iter_content.return_value = [b"replacement"]
            context = Mock(
                __enter__=Mock(return_value=response), __exit__=Mock(return_value=False)
            )
            with (
                patch.object(tools.requests, "get", return_value=context),
                patch.object(tools.os, "replace", side_effect=PermissionError),
            ):
                with self.assertRaises(PermissionError):
                    tools.download_file("https://example.com/asset", str(dest))
            self.assertEqual(dest.read_text(), "original")
            self.assertFalse(list(Path(td).glob("*.part-*")))

    def test_download_byte_limit_is_enforced_without_content_length(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            response = Mock()
            response.headers = {}
            response.iter_content.return_value = [b"x" * 1048577]
            context = Mock(
                __enter__=Mock(return_value=response), __exit__=Mock(return_value=False)
            )
            with (
                patch.object(tools.requests, "get", return_value=context),
                patch.object(tools, "settings", return_value={"max_download_mb": 1}),
            ):
                with self.assertRaisesRegex(ValueError, "limit"):
                    tools.download_file("https://example.com/asset", "asset.txt")
            self.assertFalse((Path(td) / "asset.txt").exists())

    def test_invalid_media_ratio_is_bounded(self):
        self.assertEqual(production._ratio("0/0", 24), 24)
        self.assertEqual(production._ratio("bad", 24), 24)


@unittest.skipUnless(os.name == "nt", "Windows owned process trees")
class JobRecovery(unittest.TestCase):
    def wait_result(self, folder, timeout=15):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            result = common.read_json(folder / "result.json")
            if result and not any(
                work_tools.live_process(common.read_json(folder / name, {}))
                for name in ("runner.json", "launcher.json")
            ):
                return result
            time.sleep(0.05)
        self.fail("Job did not publish a result")

    def test_fast_job_and_same_name_are_idempotent(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            command = [
                sys.executable,
                "-c",
                'from pathlib import Path; p=Path("runs.txt"); p.write_text(p.read_text()+"x" if p.exists() else "x")',
            ]
            work_tools.start_job("fast", command)
            folder = Path(td) / "Jobs/fast"
            result = self.wait_result(folder)
            self.assertEqual(result["exit_code"], 0, result)
            work_tools.start_job("fast", command)
            self.assertEqual((Path(td) / "runs.txt").read_text(), "x")

    def test_job_survives_process_that_launched_it(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            marker = task / "detached-finished.txt"
            helper = task / "launch-helper.py"
            command_code = (
                "import time; from pathlib import Path; "
                "time.sleep(1); "
                f"Path({str(marker)!r}).write_text('survived', encoding='utf-8')"
            )
            helper.write_text(
                "import os, sys, time\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(ROOT / 'app')!r})\n"
                f"os.environ['TASK_CONTINUITY_TASK_DIR'] = {str(task)!r}\n"
                "import common, work_tools\n"
                f"common.atomic_json(Path({str(task)!r}) / 'task.json', "
                "{'deadline': time.time() + 30})\n"
                f"work_tools.start_job('detached', [sys.executable, '-c', {command_code!r}], timeout=30)\n",
                encoding="utf-8",
            )
            launched = subprocess.run(
                [sys.executable, str(helper)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(launched.returncode, 0, launched.stderr)
            result = self.wait_result(task / "Jobs" / "detached", timeout=15)
            self.assertEqual(result["exit_code"], 0, result)
            self.assertEqual(marker.read_text(encoding="utf-8"), "survived")

    def test_uncertain_launch_is_never_replayed(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            command = [sys.executable, "-c", "pass"]
            with patch.object(
                work_tools.subprocess, "Popen", side_effect=OSError("spawn uncertain")
            ):
                with self.assertRaises(OSError):
                    work_tools.start_job("uncertain", command)
            with patch.object(work_tools, "_launch_job_runner") as launch:
                info = work_tools.start_job("uncertain", command)
                launch.assert_not_called()
                self.assertEqual(info["status"], "interrupted")

    def test_cancel_before_runner_spawn_has_no_side_effect(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            folder = task / "Jobs/cancelled"
            folder.mkdir(parents=True)
            common.atomic_json(
                folder / "job.json",
                {
                    "argv": [
                        sys.executable,
                        "-c",
                        'from pathlib import Path;Path("bad.txt").write_text("bad")',
                    ],
                    "cwd": str(task),
                    "timeout_seconds": 30,
                },
            )
            common.atomic_json(folder / "cancel.json", {"reason": "stopped"})
            job_runner.main(folder)
            self.assertFalse((task / "bad.txt").exists())
            self.assertEqual(common.read_json(folder / "result.json")["exit_code"], -1)

    def test_killing_runner_also_kills_child(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            task = Path(td)
            work_tools.start_job(
                "owned", [sys.executable, "-c", "import time; time.sleep(30)"]
            )
            folder = task / "Jobs/owned"
            try:
                until = time.monotonic() + 10
                while (
                    not (folder / "process.json").exists() and time.monotonic() < until
                ):
                    time.sleep(0.05)
                runner = work_tools.live_process(
                    common.read_json(folder / "runner.json", {})
                )
                child_info = common.read_json(folder / "process.json", {})
                self.assertIsNotNone(runner)
                self.assertIsNotNone(work_tools.live_process(child_info))
                runner.kill()
                runner.wait(timeout=5)
                until = time.monotonic() + 5
                while work_tools.live_process(child_info) and time.monotonic() < until:
                    time.sleep(0.05)
                self.assertIsNone(work_tools.live_process(child_info))
            finally:
                work_tools.stop_task_jobs(task)

    def test_task_deadline_stops_job(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            task = Path(td)
            common.atomic_json(task / "task.json", {"deadline": time.time() + 0.8})
            work_tools.start_job(
                "deadline", [sys.executable, "-c", "import time;time.sleep(30)"]
            )
            result = self.wait_result(task / "Jobs/deadline")
            self.assertEqual(result["exit_code"], -1)
            self.assertIn("time limit", result["error"])
            until = time.monotonic() + 5
            while work_tools.owned_job_pids(task) and time.monotonic() < until:
                time.sleep(0.05)
            self.assertFalse(work_tools.owned_job_pids(task))


class DesktopSafety(unittest.TestCase):
    def test_clipboard_text_is_restored_when_paste_fails(self):
        clipboard = Mock()
        clipboard.paste.return_value = "previous clipboard text"
        input_api = Mock()
        input_api.hotkey.side_effect = RuntimeError("input failed")
        windows = Mock()
        windows.GetForegroundWindow.return_value = 123
        with (
            patch.dict(
                sys.modules,
                {"pyautogui": input_api, "pyperclip": clipboard, "win32gui": windows},
            ),
            patch.object(tools, "unlocked_desktop"),
            patch.object(
                tools, "desktop_observation", ("observed", 123, time.monotonic())
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "input failed"):
                tools.desktop("type", text="new text", observation="observed")
        self.assertEqual(
            clipboard.copy.call_args_list[-1].args, ("previous clipboard text",)
        )

    def test_changed_foreground_prevents_desktop_input(self):
        input_api = Mock()
        windows = Mock()
        windows.GetForegroundWindow.return_value = 456
        with (
            patch.dict(sys.modules, {"pyautogui": input_api, "win32gui": windows}),
            patch.object(tools, "unlocked_desktop"),
            patch.object(
                tools, "desktop_observation", ("observed", 123, time.monotonic())
            ),
        ):
            with self.assertRaisesRegex(ValueError, "foreground changed"):
                tools.desktop("click", observation="observed")
        input_api.click.assert_not_called()


class Supervisor(unittest.TestCase):
    def test_remote_provider_never_restarts_a_local_model(self):
        cfg = {
            **copy.deepcopy(common.DEFAULT_SETTINGS),
            "api_base": "http://192.0.2.1:1234/v1",
        }
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(agent, "settings", return_value=cfg),
            patch.object(agent.subprocess, "run") as run,
        ):
            self.assertFalse(agent._start_lmstudio_server(cfg))
            self.assertFalse(agent.recover_model(Path(td), force=True))
            run.assert_not_called()

    def test_recovery_preserves_instance_identifier_and_loads_model_key(self):
        cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
        cfg["preferred_model"] = "my-instance"
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            common.atomic_json(
                task / "task.json", {"model": "my-instance", "model_key": "model-file"}
            )
            listed = Mock()
            listed.json.return_value = {"data": [{"id": "my-instance"}]}
            with (
                patch.object(agent, "settings", return_value=cfg),
                patch.object(agent, "_lmstudio_cli", return_value=Path(sys.executable)),
                patch.object(agent.requests, "get", return_value=listed),
                patch.object(
                    agent.subprocess, "run", return_value=Mock(returncode=0)
                ) as run,
            ):
                self.assertTrue(agent.recover_model(task, force=True))
            self.assertEqual(
                run.call_args_list[0].args[0][1:], ["unload", "my-instance"]
            )
            loaded = run.call_args_list[1].args[0]
            self.assertEqual(loaded[1:3], ["load", "model-file"])
            self.assertEqual(loaded[loaded.index("--identifier") + 1], "my-instance")

    def test_external_checkpoint_files_count_as_real_progress(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as out:
            task = Path(td)
            contract = acceptance.initialize_contract(task, "Fix the Python project")
            target = Path(out) / "main.py"
            target.write_text("pass")
            common.atomic_json(
                task / "progress.json",
                {
                    "objective_id": contract["objective_id"],
                    "deliverables": [str(target)],
                },
            )
            state = {}
            self.assertTrue(runtime_policy.progress_tick(state, task, contract)[0])
            self.assertFalse(runtime_policy.progress_tick(state, task, contract)[0])
            target.write_text("print(123)")
            self.assertTrue(runtime_policy.progress_tick(state, task, contract)[0])

    def test_paused_task_still_obeys_deadline(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Explain Python")
            common.atomic_json(
                task / "task.json",
                {"objective": "Explain Python", "deadline": time.time() + 0.1},
            )
            agent.STOP.clear()
            agent.WAKE.clear()
            try:
                with (
                    patch.object(agent, "keep_awake"),
                    patch.object(agent, "model_info") as model,
                ):
                    agent.worker(task)
                    model.assert_not_called()
                self.assertEqual(
                    common.read_json(task / "task.json")["status"], "time_limit"
                )
            finally:
                agent.WAKE.set()
                agent.STOP.clear()

    def test_preferred_model_does_not_silently_switch(self):
        cfg = {
            **copy.deepcopy(common.DEFAULT_SETTINGS),
            "provider": "openai-compatible",
            "preferred_model": "absent",
        }
        response = Mock()
        response.json.return_value = {"data": [{"id": "other"}]}
        with (
            patch.object(agent, "settings", return_value=cfg),
            patch.object(agent.requests, "get", return_value=response),
        ):
            with self.assertRaisesRegex(RuntimeError, "preferred_model"):
                agent.model_info()


if __name__ == "__main__":
    unittest.main()
