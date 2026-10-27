"""Recovery must preserve model choice and bound work beyond the model loop."""

import copy
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import acceptance
import agent
import common
import runtime_policy


class ModelIdentity(unittest.TestCase):
    def test_explicit_replacement_loads_new_model_without_unloading_saved_model(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            common.atomic_json(
                task / "task.json", {"model": "old-instance", "model_key": "old-key"}
            )
            cfg = {
                **copy.deepcopy(common.DEFAULT_SETTINGS),
                "preferred_model": "replacement",
            }
            response = Mock()
            response.json.return_value = {"data": [{"id": "old-instance"}]}
            with (
                patch.object(agent, "settings", return_value=cfg),
                patch.object(agent, "_lmstudio_cli", return_value=Path(sys.executable)),
                patch.object(agent.requests, "get", return_value=response),
                patch.object(
                    agent.subprocess, "run", return_value=Mock(returncode=0)
                ) as run,
            ):
                self.assertTrue(agent.recover_model(task, force=True))
            run.assert_called_once()
            command = run.call_args.args[0]
            self.assertEqual(command[1:3], ["load", "replacement"])
            self.assertEqual(command[command.index("--identifier") + 1], "replacement")

    def test_generic_task_keeps_its_model_when_listing_order_changes(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            common.atomic_json(task / "task.json", {"model": "chosen"})
            cfg = {
                **copy.deepcopy(common.DEFAULT_SETTINGS),
                "provider": "openai-compatible",
            }
            response = Mock()
            response.json.return_value = {"data": [{"id": "other"}, {"id": "chosen"}]}
            with (
                patch.object(agent, "ACTIVE", task),
                patch.object(agent, "settings", return_value=cfg),
                patch.object(agent.requests, "get", return_value=response),
            ):
                self.assertEqual(agent.model_info()["model"], "chosen")
                response.json.return_value = {"data": [{"id": "other"}]}
                with self.assertRaisesRegex(RuntimeError, "selected model"):
                    agent.model_info()

    def test_lmstudio_selects_exact_loaded_instance_and_recovers_same_model(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            common.atomic_json(
                task / "task.json", {"model": "second", "model_key": "same-model"}
            )
            cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
            response = Mock()
            response.json.return_value = {
                "models": [
                    {
                        "key": "other",
                        "type": "llm",
                        "loaded_instances": [{"id": "other"}],
                    },
                    {
                        "key": "same-model",
                        "type": "llm",
                        "loaded_instances": [{"id": "first"}, {"id": "second"}],
                    },
                ]
            }
            with (
                patch.object(agent, "ACTIVE", task),
                patch.object(agent, "settings", return_value=cfg),
                patch.object(agent.requests, "get", return_value=response),
            ):
                self.assertEqual(agent.model_info()["model"], "second")
                response.json.return_value["models"][1]["loaded_instances"] = [
                    {"id": "reloaded"}
                ]
                self.assertEqual(agent.model_info()["model"], "reloaded")
                cfg["preferred_model"] = "first"
                response.json.return_value["models"][1]["loaded_instances"] = [
                    {"id": "reloaded"},
                    {"id": "first"},
                ]
                self.assertEqual(agent.model_info()["model"], "first")


class SupervisorBounds(unittest.TestCase):
    def test_actor_cleanup_preserves_verified_live_blender_logger(self):
        for preserve_jobs in (False, True):
            with self.subTest(preserve_jobs=preserve_jobs):
                parent, helper, core = Mock(pid=100), Mock(pid=101), Mock(pid=102)
                core.name.return_value = "python.exe"
                parent.children.return_value = [helper, core]
                with (
                    patch.object(agent.psutil, "Process", return_value=parent),
                    patch.object(agent, "_tracked_blender_logger", return_value=helper),
                    patch.object(agent, "owned_job_pids", return_value=set()),
                    patch.object(agent.psutil, "wait_procs", return_value=([], [])),
                ):
                    agent.terminate_tree(parent, preserve_jobs=preserve_jobs)
                helper.terminate.assert_not_called()
                core.terminate.assert_called_once()
                parent.terminate.assert_called_once()

    def test_live_job_pauses_wall_stall_window_without_inventing_output_progress(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            state = {}
            contract = {"requires_action": True, "durable_deliverable_required": True}
            runtime_policy.progress_tick(state, task, contract, now=0)
            self.assertFalse(
                runtime_policy.progress_tick(
                    state, task, contract, live_job=True, now=3600
                )[0]
            )
            self.assertEqual(state["progress_window_started"], 3600)
            self.assertEqual(state["last_artifact_change"], 0)
            self.assertEqual(
                runtime_policy.progress_tick(state, task, contract, now=3601),
                (False, 1),
            )

    def test_restoring_snapshot_does_not_invent_a_file_change(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            (task / "output.txt").write_text("unchanged")
            contract = {"requires_action": True, "durable_deliverable_required": True}
            state = {}
            runtime_policy.progress_tick(state, task, contract, now=0)
            state["stagnant_steps"] = 29
            common.atomic_json(task / "agent-state.json", state)
            restored = common.read_json(task / "agent-state.json")
            self.assertEqual(
                runtime_policy.progress_tick(restored, task, contract, now=1),
                (False, 30),
            )

    def run_worker(self, task, stop, **patches):
        wake = threading.Event()
        wake.set()
        with (
            patch.multiple(
                agent,
                ACTIVE=task,
                PROC=None,
                STOP=stop,
                WAKE=wake,
                STEER=threading.Event(),
            ),
            patch.object(agent, "keep_awake"),
            patch.multiple(agent, **patches),
        ):
            agent.worker(task)

    def test_disabling_auto_recovery_does_not_start_provider_server(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Explain this result")
            common.atomic_json(
                task / "task.json",
                {"objective": "Explain this result", "deadline": time.time() + 10},
            )
            stop = threading.Event()

            def unavailable():
                stop.set()
                raise agent.requests.ConnectionError("offline")

            start = Mock()
            self.run_worker(
                task,
                stop,
                model_info=Mock(side_effect=unavailable),
                settings=Mock(
                    return_value={
                        **common.DEFAULT_SETTINGS,
                        "auto_model_recovery": False,
                    }
                ),
                _start_lmstudio_server=start,
            )
            start.assert_not_called()

    def test_missing_pinned_model_still_enters_bounded_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Explain this result")
            common.atomic_json(
                task / "task.json",
                {"objective": "Explain this result", "deadline": time.time() + 10},
            )
            stop = threading.Event()

            def unavailable():
                stop.set()
                raise RuntimeError("The task's selected model is not loaded; reload it")

            recovery = Mock()
            self.run_worker(
                task,
                stop,
                model_info=Mock(side_effect=unavailable),
                recover_model=recovery,
                settings=Mock(return_value=copy.deepcopy(common.DEFAULT_SETTINGS)),
            )
            recovery.assert_called_once_with(task, force=True)

    def test_verification_cannot_accept_result_after_task_deadline(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Explain this result")
            common.atomic_json(
                task / "task.json",
                {"objective": "Explain this result", "deadline": time.time() + 10},
            )
            common.atomic_json(
                task / "result.json",
                {
                    "status": "completed",
                    "summary": "answer",
                    "verification": "checked",
                    "files": [],
                },
            )

            def expires(*_):
                meta = common.read_json(task / "task.json")
                meta["deadline"] = time.time() - 1
                common.atomic_json(task / "task.json", meta)

            discover = Mock()
            self.run_worker(
                task,
                threading.Event(),
                validate_completion=Mock(side_effect=expires),
                model_info=discover,
            )
            self.assertEqual(
                common.read_json(task / "task.json")["status"], "time_limit"
            )
            discover.assert_not_called()

    def test_unrelated_files_and_empty_directories_count_toward_scan_limit(self):
        for directories in (False, True):
            with (
                self.subTest(directories=directories),
                tempfile.TemporaryDirectory() as td,
            ):
                task = Path(td)
                for index in range(20):
                    path = task / f"entry-{index}"
                    if directories:
                        path.mkdir()
                    else:
                        path.with_suffix(".unrelated").write_text("ignored")
                with patch.object(runtime_policy, "SNAPSHOT_ENTRY_LIMIT", 5):
                    result = runtime_policy.artifact_snapshot(
                        task, {"requires_action": True, "required_extensions": [".py"]}
                    )
                self.assertEqual(
                    result,
                    [("__snapshot_truncated__", runtime_policy.SNAPSHOT_FILE_LIMIT, 5)],
                )


if __name__ == "__main__":
    unittest.main()
