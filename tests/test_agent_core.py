import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import acceptance
import agent
import common
import runtime_policy
import work_tools


class Contracts(unittest.TestCase):
    def make(self, text):
        with tempfile.TemporaryDirectory() as td:
            return acceptance.initialize_contract(Path(td), text)

    def test_question(self):
        self.assertFalse(self.make("How does a Python venv work?")["requires_action"])

    def test_csv(self):
        contract = self.make("Create a clean report.csv and verify it.")
        self.assertTrue(contract["requires_action"])
        self.assertIn(".csv", contract["required_extensions"])

    def test_blender_no_video(self):
        contract = self.make("Create a Blender scene with no animation.")
        self.assertIn(".blend", contract["required_extensions"])
        self.assertNotIn(".mp4", contract["required_extensions"])

    def test_video(self):
        contract = self.make(
            "Create a 10 second video at 24 fps, 1920x1080, export MP4."
        )
        self.assertEqual(contract["video"]["frames"], 240)
        self.assertEqual(contract["video"]["width"], 1920)
        self.assertIn(".mp4", contract["required_extensions"])

    def test_negative_list_does_not_become_positive(self):
        contract = self.make(
            "Create a fresh Blender scene; no assets, research, animation or long brief "
            "needed. Render preview.png."
        )
        self.assertEqual(set(contract["required_extensions"]), {".blend", ".png"})

    def test_positive_action_after_negative_clause_survives(self):
        contract = self.make("Build a Blender project, no video, render preview.png.")
        self.assertEqual(set(contract["required_extensions"]), {".blend", ".png"})

    def test_blocked_completion_requires_action(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Create report.md.")
            with self.assertRaisesRegex(ValueError, "exact user action"):
                acceptance.validate_completion(
                    task,
                    {
                        "status": "blocked",
                        "summary": "Blocked",
                        "verification": "Verified blocker.",
                        "required_user_action": "",
                        "files": [],
                    },
                )

    def test_system_action_can_finish_without_file(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            contract = acceptance.initialize_contract(
                task,
                "Install and configure the local package.",
            )
            self.assertTrue(contract["requires_action"])
            self.assertEqual(contract["required_extensions"], [])
            acceptance.validate_completion(
                task,
                {
                    "status": "completed",
                    "summary": "Configured.",
                    "verification": "Read back the resulting system state.",
                    "required_user_action": "",
                    "files": [],
                },
            )

    def test_external_json_deliverable_is_allowed_and_validated(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as out:
            task = Path(td)
            output = Path(out) / "result.json"
            acceptance.initialize_contract(task, "Create result.json.")
            output.write_text("{", encoding="utf-8")
            report = {
                "status": "completed",
                "summary": "Created JSON.",
                "verification": "Parsed the file.",
                "required_user_action": "",
                "files": [{"path": str(output), "bytes": output.stat().st_size}],
            }
            with self.assertRaisesRegex(ValueError, "structurally valid"):
                acceptance.validate_completion(task, report)

            output.write_text('{"ok": true}', encoding="utf-8")
            report["files"][0]["bytes"] = output.stat().st_size
            acceptance.validate_completion(task, report)


class Progress(unittest.TestCase):
    def test_code_counts(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            contract = {
                "requires_action": True,
                "required_extensions": [],
                "visual_review_required": False,
            }
            self.assertEqual(runtime_policy.artifact_snapshot(folder, contract), [])
            (folder / "main.py").write_text("print('ok')", encoding="utf-8")
            self.assertEqual(
                runtime_policy.artifact_snapshot(folder, contract)[0][0],
                "main.py",
            )

    def test_helpers_do_not_count(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "Scripts").mkdir()
            (folder / "Scripts" / "x.py").write_text("pass", encoding="utf-8")
            self.assertEqual(
                runtime_policy.artifact_snapshot(folder, {"requires_action": True}),
                [],
            )

    def test_dependency_trees_are_pruned(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "node_modules" / "pkg").mkdir(parents=True)
            (folder / "node_modules" / "pkg" / "index.js").write_text(
                "ignored",
                encoding="utf-8",
            )
            (folder / "src").mkdir()
            (folder / "src" / "main.js").write_text("kept", encoding="utf-8")
            snapshot = runtime_policy.artifact_snapshot(
                folder,
                {"requires_action": True},
            )
            self.assertEqual([row[0] for row in snapshot], [str(Path("src/main.js"))])

    def test_required_extension_filters_unrelated_repo_churn(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "notes.md").write_text("noise", encoding="utf-8")
            (folder / "main.py").write_text("print('ok')", encoding="utf-8")
            snapshot = runtime_policy.artifact_snapshot(
                folder,
                {
                    "requires_action": True,
                    "required_extensions": [".py"],
                    "visual_review_required": False,
                },
            )
            self.assertEqual([row[0] for row in snapshot], ["main.py"])

    def test_state_change_receipt_counts_for_no_file_task(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            contract = {
                "requires_action": True,
                "required_extensions": [],
                "visual_review_required": False,
                "durable_deliverable_required": False,
            }
            state = {
                "artifact_snapshot": [],
                "stagnant_steps": 5,
                "progress_window_started": 1.0,
                "receipts": [],
            }
            changed, stagnant = runtime_policy.progress_tick(
                state, folder, contract, now=10.0
            )
            self.assertFalse(changed)
            self.assertEqual(stagnant, 6)

            state["receipts"] = [
                {"tool": "shell", "ok": True, "at": 11.0, "job_name": "configure"}
            ]
            changed, stagnant = runtime_policy.progress_tick(
                state, folder, contract, now=12.0
            )
            self.assertTrue(changed)
            self.assertEqual(stagnant, 0)

            state["receipts"] = [
                {"tool": "read_text", "ok": True, "at": 13.0, "path": "x"}
            ]
            changed, stagnant = runtime_policy.progress_tick(
                state, folder, contract, now=14.0
            )
            self.assertFalse(changed)
            self.assertEqual(stagnant, 1)


class Provider(unittest.TestCase):
    def test_generic_discovery(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": [{"id": "a"}, {"id": "my-model"}]}
        cfg = {
            **common.DEFAULT_SETTINGS,
            "provider": "openai-compatible",
            "api_base": "http://127.0.0.1:9999/v1",
            "preferred_model": "my-model",
            "context_length": 32768,
        }
        with (
            patch.object(agent, "settings", return_value=cfg),
            patch.object(agent.requests, "get", return_value=response) as get,
        ):
            info = agent.model_info()
        self.assertEqual(info["model"], "my-model")
        self.assertEqual(info["model_key"], "my-model")
        self.assertEqual(info["context"], 32768)
        get.assert_called_once_with(
            "http://127.0.0.1:9999/v1/models",
            timeout=5,
        )


class Configuration(unittest.TestCase):
    def test_output_budget_clamps_to_context(self):
        cfg = {
            **common.DEFAULT_SETTINGS,
            "provider": "openai-compatible",
            "context_length": 4096,
            "max_output_tokens": 4096,
        }
        validated = common.validate_settings(cfg)
        self.assertEqual(validated["max_output_tokens"], 2048)

    def test_legacy_blender_switch_migrates(self):
        cfg = {
            **common.DEFAULT_SETTINGS,
            "blender_enabled": True,
            "capabilities": {
                **common.DEFAULT_SETTINGS["capabilities"],
                "blender": False,
            },
        }
        validated = common.validate_settings(cfg)
        self.assertTrue(validated["capabilities"]["blender"])
        self.assertNotIn("blender_enabled", validated)


class PathSemantics(unittest.TestCase):
    def test_relative_paths_anchor_to_task(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(
                os.environ,
                {"TASK_CONTINUITY_TASK_DIR": td},
            ),
        ):
            root = Path(td).resolve()
            self.assertEqual(
                common.resolve_path("nested/out.txt"),
                root / "nested" / "out.txt",
            )
            info = json.loads(work_tools.write_text("result.txt", "ok"))
            self.assertEqual(Path(info["path"]), root / "result.txt")
            read = json.loads(work_tools.read_text("result.txt"))
            self.assertEqual(read["text"], "ok")

    def test_task_tools_cannot_modify_agent_source(self):
        with self.assertRaisesRegex(ValueError, "source/runtime"):
            work_tools.writable(ROOT / "README.md")


@unittest.skipUnless(sys.platform == "win32", "Windows process semantics")
class DurableJobs(unittest.TestCase):
    def test_stop_waits_for_owned_job_tree(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(
                os.environ,
                {"TASK_CONTINUITY_TASK_DIR": td},
            ),
        ):
            task = Path(td)
            common.atomic_json(
                task / "task.json",
                {"deadline": time.time() + 60},
            )
            status = work_tools.start_job(
                "stop-test",
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(30)",
                ],
                timeout=60,
            )
            self.assertEqual(status["status"], "running")
            work_tools.stop_task_jobs(task)
            result = common.read_json(task / "Jobs" / "stop-test" / "result.json")
            self.assertEqual(result["exit_code"], -1)
            self.assertFalse(work_tools.owned_job_pids(task))


class Hygiene(unittest.TestCase):
    def test_no_private_paths(self):
        bad = [
            "C:" + "\\Qwen" + "Agent",
            "Hao" + " Ran",
            "qwen3.8" + "-27b@",
            "Qwen" + "AgentChat",
            "Qwen" + " Agent",
        ]
        for path in (ROOT / "app").glob("*"):
            if path.suffix.lower() not in {".py", ".html"}:
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            for needle in bad:
                self.assertNotIn(needle, source, f"{needle} in {path.name}")

    def test_blender_off_default(self):
        cfg = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["preferred_model"], "")
        self.assertNotIn("blender_enabled", cfg)
        self.assertNotIn("interpreter_exe", cfg)
        self.assertFalse(cfg["capabilities"]["blender"])

    def test_data_is_gitignored(self):
        source = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/", source)
        self.assertIn(".venv/", source)


if __name__ == "__main__":
    unittest.main()
