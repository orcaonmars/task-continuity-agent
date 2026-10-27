"""Regression coverage for multi-day job identity, cleanup and render recovery."""

import json
import io
import os
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import common
import job_runner
import production
import work_tools
import tools


class JobIdentity(unittest.TestCase):
    def test_relative_cwd_and_script_hash_use_the_task_directory(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
            patch.object(work_tools, "_launch_job_runner"),
        ):
            working = Path(td) / "project"
            working.mkdir()
            source = working / "script.py"
            source.write_text("print('first')", encoding="utf-8")
            command = [sys.executable, "script.py"]
            work_tools.start_job("relative", command, cwd="project")
            spec = common.read_json(Path(td) / "Jobs/relative/job.json")
            self.assertEqual(spec["cwd"], str(working.resolve()))
            self.assertEqual(
                spec["input_files"], {str(source.resolve()): common.file_sha256(source)}
            )
            source.write_text("print('changed')", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "another command"):
                work_tools.start_job("relative", command, cwd="project")

    def test_same_command_in_different_directory_needs_new_name(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
            patch.object(work_tools, "_launch_job_runner"),
        ):
            (Path(td) / "other").mkdir()
            command = [sys.executable, "-c", "pass"]
            work_tools.start_job("identity", command)
            with self.assertRaisesRegex(ValueError, "another command"):
                work_tools.start_job("identity", command, cwd="other")

    def test_relative_script_syntax_is_checked_before_claiming_name(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
            patch.object(work_tools, "_launch_job_runner") as launch,
        ):
            (Path(td) / "broken.py").write_text("if :", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Script syntax error"):
                work_tools.start_job("syntax", [sys.executable, "broken.py"])
            launch.assert_not_called()
            self.assertFalse((Path(td) / "Jobs/syntax").exists())

    def test_fifty_hour_job_is_not_silently_cut_to_forty_eight_hours(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
            patch.object(work_tools, "_launch_job_runner"),
        ):
            command = [sys.executable, "-c", "pass"]
            for name, requested, expected in (
                ("fifty", 50 * 3600, 50 * 3600),
                ("bounded", 100 * 3600, 72 * 3600),
            ):
                work_tools.start_job(name, command, timeout=requested)
                self.assertEqual(
                    common.read_json(Path(td) / f"Jobs/{name}/job.json")[
                        "timeout_seconds"
                    ],
                    expected,
                )

    def test_stopping_finished_job_preserves_original_result(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
        ):
            folder = Path(td) / "Jobs/done"
            common.atomic_json(folder / "job.json", {"argv": ["fixture"]})
            finished = {"exit_code": 0, "finished_at": 123.0}
            common.atomic_json(folder / "result.json", finished)
            response = json.loads(work_tools.jobs("stop", "done"))
            self.assertEqual(response["result"], finished)
            self.assertFalse((folder / "cancel.json").exists())


class RunnerFinalization(unittest.TestCase):
    def make_job(self, task, command="import time;time.sleep(30)", timeout=1):
        folder = task / "Jobs/fixture"
        common.atomic_json(
            folder / "job.json",
            {
                "argv": [sys.executable, "-c", command],
                "cwd": str(task),
                "timeout_seconds": timeout,
            },
        )
        return folder

    def test_both_newline_free_pipes_drain_with_bounded_real_logs(self):
        with tempfile.TemporaryDirectory() as td:
            folder = self.make_job(
                Path(td),
                "import os;chunk=b'x'*65536;"
                "[(os.write(1,chunk),os.write(2,chunk)) for _ in range(128)]",
                timeout=10,
            )
            with patch.object(
                job_runner, "settings", return_value={"raw_log_max_mb": 1}
            ):
                job_runner.main(folder)
            result = common.read_json(folder / "result.json")
            self.assertEqual(result["exit_code"], 0, result)
            logs = [path for path in folder.iterdir() if ".log" in path.name]
            self.assertEqual(len(logs), 6)
            self.assertLessEqual(
                sum(path.stat().st_size for path in logs),
                6 * (1024 * 1024 + 65536),
            )
            self.assertEqual(common.tail_bytes(folder / "stdout.log", 10), b"x" * 10)
            self.assertEqual(common.tail_bytes(folder / "stderr.log", 10), b"x" * 10)

    def test_locked_append_drains_and_recovers_without_failing_job(self):
        with tempfile.TemporaryDirectory() as td:
            folder = self.make_job(
                Path(td), "import os;os.write(1,b'x'*262144)", timeout=10
            )
            original_open = Path.open
            failures = []

            def locked_once(path, *args, **kwargs):
                if path.name == "stdout.log" and not failures:
                    failures.append(True)
                    raise PermissionError("temporary viewer lock")
                return original_open(path, *args, **kwargs)

            with (
                patch.object(Path, "open", locked_once),
                patch.object(
                    job_runner, "settings", return_value={"raw_log_max_mb": 1}
                ),
            ):
                job_runner.main(folder)
            result = common.read_json(folder / "result.json")
            self.assertEqual(result["exit_code"], 0, result)
            self.assertGreater(result["log_dropped_bytes"]["stdout"], 0)
            self.assertEqual(common.tail_bytes(folder / "stdout.log", 10), b"x" * 10)

    def test_diagnostic_io_failure_keeps_draining_when_requested(self):
        with tempfile.TemporaryDirectory() as td:
            stream = io.BufferedReader(io.BytesIO(b"x" * 131072))
            errors, losses = [], {}
            with patch.object(Path, "open", side_effect=OSError("disk unavailable")):
                job_runner._copy_log(
                    stream, Path(td) / "diagnostic.log", 1024, errors, losses, True
                )
            self.assertTrue(stream.closed)
            self.assertFalse(errors)
            self.assertEqual(losses["dropped_bytes"], 131072)

    def test_expired_task_cannot_launch_delayed_runner(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            folder = self.make_job(task)
            common.atomic_json(task / "task.json", {"deadline": time.time() - 1})
            with patch.object(job_runner, "_spawn_owned") as spawn:
                job_runner.main(folder)
            spawn.assert_not_called()
            self.assertIn(
                "time limit", common.read_json(folder / "result.json")["error"]
            )

    def test_timeout_publishes_only_after_ownership_handle_and_child_are_closed(self):
        with tempfile.TemporaryDirectory() as td:
            folder = self.make_job(Path(td))
            original_spawn = job_runner._spawn_owned
            original_write = job_runner.atomic_json
            events = []
            spawned = []

            def spawn(spec):
                process, owned = original_spawn(spec)
                spawned.append(process)

                class Ownership:
                    def Close(self):
                        if owned:
                            owned.Close()
                        events.append("ownership closed")

                return process, Ownership()

            def write(path, value):
                if Path(path).name == "result.json":
                    self.assertEqual(events, ["ownership closed"])
                    self.assertIsNotNone(spawned[0].poll())
                    events.append("result published")
                return original_write(path, value)

            with (
                patch.object(job_runner, "_spawn_owned", side_effect=spawn),
                patch.object(job_runner, "atomic_json", side_effect=write),
                patch.object(
                    job_runner, "settings", return_value={"raw_log_max_mb": 1}
                ),
            ):
                job_runner.main(folder)
            self.assertEqual(events, ["ownership closed", "result published"])
            self.assertEqual(common.read_json(folder / "result.json")["exit_code"], -1)

    def test_cancellation_arriving_after_command_exit_cannot_publish_success(self):
        with tempfile.TemporaryDirectory() as td:
            folder = self.make_job(Path(td), "pass", timeout=10)
            original_spawn = job_runner._spawn_owned

            def spawn(spec):
                process, owned = original_spawn(spec)

                class Ownership:
                    def Close(self):
                        common.atomic_json(
                            folder / "cancel.json", {"reason": "stopped"}
                        )
                        if owned:
                            owned.Close()

                return process, Ownership()

            with (
                patch.object(job_runner, "_spawn_owned", side_effect=spawn),
                patch.object(
                    job_runner, "settings", return_value={"raw_log_max_mb": 1}
                ),
            ):
                job_runner.main(folder)
            result = common.read_json(folder / "result.json")
            self.assertEqual(result["exit_code"], -1)
            self.assertEqual(result["error"], "Job cancelled")


class RenderRecovery(unittest.TestCase):
    def test_fitting_camera_cannot_overwrite_existing_project(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
            patch.object(work_tools, "capability_enabled", return_value=True),
            patch.object(work_tools, "find_blender", return_value="blender.exe"),
            patch.object(work_tools, "start_job") as launch,
        ):
            project = Path(td) / "valuable.blend"
            original = b"BLENDER preserved fixture"
            project.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "new .blend"):
                work_tools.blender_project("fit_camera", "fit", output=str(project))
            self.assertEqual(project.read_bytes(), original)
            launch.assert_not_called()


class OptionalResourceRecovery(unittest.TestCase):
    def test_failed_bridge_startup_stops_only_identified_launch_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            child = Mock(pid=111)
            logger_process = Mock(pid=222)
            logger_process.children.return_value = [child]
            logger = Mock()

            def launched(argv, **kwargs):
                self.assertEqual(argv[1], str(common.APP_DIR / "blender_launcher.py"))
                if os.name == "nt":
                    self.assertTrue(kwargs["creationflags"] & 0x01000000)
                common.atomic_json(
                    root / "State/live-blender.json", {"launch_id": argv[-1]}
                )
                return logger

            with (
                patch.object(common, "DATA_ROOT", root),
                patch.object(common, "blender_rpc", side_effect=ConnectionRefusedError),
                patch.object(common, "capability_enabled", return_value=True),
                patch.object(common, "settings", return_value=common.DEFAULT_SETTINGS),
                patch.object(common, "find_blender", return_value="fixture-blender"),
                patch.object(
                    common, "_tracked_blender_process", side_effect=[None, child]
                ),
                patch.object(
                    common,
                    "_tracked_blender_logger",
                    side_effect=[None, logger_process],
                ),
                patch.object(common.subprocess, "Popen", side_effect=launched),
                patch.object(common.time, "monotonic", side_effect=[0, 100]),
                self.assertRaisesRegex(
                    RuntimeError, "failed startup instance was stopped"
                ),
            ):
                common.start_blender()
            child.terminate.assert_called_once()
            logger_process.terminate.assert_called_once()

    def test_bootstrap_diagnostics_rotate_and_locks_do_not_block_startup(self):
        with tempfile.TemporaryDirectory() as td:
            fake_bpy = SimpleNamespace(
                app=SimpleNamespace(version=(5, 0), timers=Mock()),
                context=SimpleNamespace(scene=SimpleNamespace()),
            )
            with (
                patch.dict(os.environ, {"TASK_CONTINUITY_DATA": td}),
                patch.dict(
                    sys.modules,
                    {"bpy": fake_bpy, "addon_utils": Mock(), "blender_mcp": Mock()},
                ),
            ):
                module = runpy.run_path(str(ROOT / "app/blender_bootstrap.py"))
            log = module["log"]
            for _ in range(220):
                log("x" * 16384)
            logs = list((Path(td) / "Logs").glob("blender-bootstrap-errors.log*"))
            self.assertLessEqual(
                sum(path.stat().st_size for path in logs), 3 * (1024 * 1024 + 16385)
            )
            with patch.object(Path, "open", side_effect=PermissionError("locked")):
                log("locked diagnostic")
            self.assertTrue(fake_bpy.app.timers.register.called)

    def test_detached_blender_logger_survives_launcher_and_bounds_native_pipes(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            ledger = task / "State/live-blender.json"
            child = [
                sys.executable,
                "-c",
                "import time,os;time.sleep(.4);os.write(1,b'x'*4194304);os.write(2,b'y'*1048576)",
            ]
            helper = [
                sys.executable,
                str(ROOT / "app/blender_launcher.py"),
                str(ledger),
                "131072",
                json.dumps(child),
                "fixture",
            ]
            source = (
                "import subprocess;subprocess.Popen("
                + repr(helper)
                + ",stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags="
                + str(common.NO_WINDOW | (0x01000000 if os.name == "nt" else 0))
                + ")"
            )
            launcher = subprocess.run(
                [sys.executable, "-c", source], capture_output=True, timeout=10
            )
            self.assertEqual(launcher.returncode, 0, launcher.stderr)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                state = common.read_json(ledger, {})
                if state.get("finished_at"):
                    break
                time.sleep(0.05)
            self.assertEqual(state.get("exit_code"), 0, state)
            self.assertNotIn("error", state)
            self.assertIn("logger_pid", state)
            logs = list((task / "Logs").glob("blender-startup.log*"))
            self.assertEqual(len(logs), 3)
            self.assertLessEqual(
                sum(path.stat().st_size for path in logs), 3 * (131072 + 65536)
            )

    def test_failed_blender_spawn_has_durable_error_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "State/live-blender.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "app/blender_launcher.py"),
                    str(ledger),
                    "131072",
                    json.dumps([str(Path(td) / "missing.exe")]),
                    "failed",
                ],
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            state = common.read_json(ledger)
            self.assertIn("FileNotFoundError", state["error"])
            self.assertIn("finished_at", state)

    def test_scratch_cleanup_requires_dead_owned_processes_and_same_task(self):
        with (
            tempfile.TemporaryDirectory() as td,
            tempfile.TemporaryDirectory() as task,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": task}),
        ):
            root = Path(td).resolve()

            def session(name, **changes):
                folder = root / name
                folder.mkdir()
                (folder / "partial").write_text("partial download")
                owner = {
                    "kind": "browser-download-scratch-v1",
                    "root": str(root),
                    "task": str(Path(task).resolve()),
                    "session": name,
                    "ready": True,
                    "processes": [{"pid": 999999999, "created": 1}],
                }
                owner.update(changes)
                common.atomic_json(root / (name + ".owner.json"), owner)
                return folder

            dead = session("session-dead")
            live = session("session-live", processes=[tools._process_identity()])
            foreign = session("session-foreign", task="another task")
            uncertain = session("session-uncertain", ready=False)
            unmarked = root / "session-unmarked"
            unmarked.mkdir()
            tools.cleanup_browser_scratch(root)
            self.assertFalse(dead.exists())
            self.assertFalse((root / "session-dead.owner.json").exists())
            self.assertTrue(
                all(path.exists() for path in (live, foreign, uncertain, unmarked))
            )

    def test_scratch_path_escape_and_unknown_process_are_preserved(self):
        with (
            tempfile.TemporaryDirectory() as td,
            tempfile.TemporaryDirectory() as task,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": task}),
        ):
            root = Path(td).resolve()
            folder = root / "session-unknown"
            folder.mkdir()
            owner = {
                "kind": "browser-download-scratch-v1",
                "root": str(root),
                "task": str(Path(task).resolve()),
                "session": folder.name,
                "ready": True,
                "processes": [{}],
            }
            common.atomic_json(root / (folder.name + ".owner.json"), owner)
            tools.cleanup_browser_scratch(root)
            self.assertTrue(folder.exists())
            owner.update(
                session="../outside", processes=[{"pid": 999999999, "created": 1}]
            )
            common.atomic_json(root / "session-escape.owner.json", owner)
            tools.cleanup_browser_scratch(root)
            self.assertTrue((root / "session-escape.owner.json").exists())

    def test_unversioned_or_corrupt_frame_receipt_cannot_be_resumed(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}),
            patch.object(production, "capability_enabled", return_value=True),
            patch.object(production, "start_job") as launch,
        ):
            project = Path(td) / "scene.blend"
            project.write_bytes(b"BLENDER fixture")
            frames = Path(td) / "frames"
            frames.mkdir()
            (frames / "000001.png").write_bytes(b"unrelated prior frame")
            for marker in (None, "{broken", "{}"):
                with self.subTest(marker=marker):
                    if marker is not None:
                        (frames / "render-version.json").write_text(marker)
                    with self.assertRaisesRegex(ValueError, "new output folder"):
                        production.render_sequence(
                            str(project), "frames", str(frames), 1, 2
                        )
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
