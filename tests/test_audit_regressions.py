"""User-facing failure cases found during the independent November audit."""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import acceptance
import agent
import common
import work_tools


class InputAndOutputContracts(unittest.TestCase):
    def test_numerical_completion_needs_execution_covering_current_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Read sales.csv and calculate total revenue. Save report.md.")
            output = task / "report.md"
            output.write_text("Revenue: 100")
            os.utime(output, (100, 100))
            report = {"status": "completed", "summary": "Saved", "verification": "Manually checked totals",
                      "files": [{"path": "report.md"}]}
            with self.assertRaisesRegex(ValueError, "tracked calculation"):
                acceptance.validate_completion(task, report)
            job = task / "Jobs/calculate"
            common.atomic_json(job / "job.json", {"argv": ["python", "calculate.py"]})
            common.atomic_json(job / "result.json", {"exit_code": 1, "finished_at": 200})
            with self.assertRaisesRegex(ValueError, "tracked calculation"):
                acceptance.validate_completion(task, report)
            common.atomic_json(job / "result.json", {"exit_code": 0, "finished_at": 200})
            acceptance.validate_completion(task, report)
            os.utime(output, (300, 300))
            with self.assertRaisesRegex(ValueError, "current output"):
                acceptance.validate_completion(task, report)

    def test_inputs_are_not_required_outputs(self):
        cases = [
            ("Read sales.csv and save report.md.", [".md"]),
            ("Inspect screenshot.png, then write findings.md.", [".md"]),
            ("Convert input.csv to output.json.", [".json"]),
            ("Create result.json from source.csv.", [".json"]),
            ("Using photo.jpg, create a caption.txt file.", [".txt"]),
            ("Review main.py; save findings.md without editing the source.", [".md"]),
        ]
        for prompt, expected in cases:
            with self.subTest(prompt=prompt), tempfile.TemporaryDirectory() as td:
                contract = acceptance.initialize_contract(Path(td), prompt)
                self.assertEqual(contract["required_extensions"], expected)
                self.assertFalse(contract["visual_review_required"])

    def test_valid_report_does_not_require_input_as_deliverable(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Read sales.csv and save report.md.")
            (task / "report.md").write_text("# Sales report\nChecked source totals.")
            acceptance.validate_completion(task, {
                "status": "completed", "summary": "Saved report", "verification": "Checked totals",
                "files": [{"path": "report.md"}],
            })

    def test_explicitly_named_outputs_cannot_be_satisfied_by_one_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Create first.json and second.json.")
            (task / "first.json").write_text('{"ok": true}')
            report = {"status": "completed", "summary": "Saved", "verification": "Parsed",
                      "files": [{"path": "first.json"}]}
            with self.assertRaisesRegex(ValueError, "second.json"):
                acceptance.validate_completion(task, report)
            (task / "second.json").write_text('{"ok": true}')
            report["files"].append({"path": "second.json"})
            acceptance.validate_completion(task, report)

    def test_review_of_output_does_not_remove_its_original_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            contract = acceptance.initialize_contract(
                Path(td), "Create result.json, then read result.json and verify it."
            )
            self.assertEqual(contract["required_extensions"], [".json"])


def handler(path, data):
    request = agent.Handler.__new__(agent.Handler)
    body = json.dumps(data).encode()
    request.rfile = io.BytesIO(body)
    request.headers = {"Content-Length": str(len(body))}
    request.path = path
    request.send = Mock()
    return request


class TaskStateAndControls(unittest.TestCase):
    def test_python_job_preserves_spaces_and_shell_characters_literally(self):
        with tempfile.TemporaryDirectory(prefix="python job ") as td:
            folder = Path(td)
            script = folder / "check data.py"
            script.write_text("import json,sys; from pathlib import Path; Path('arguments.json').write_text(json.dumps(sys.argv[1:]))")
            arguments = ["file with spaces.csv", "$(must_not_execute)", "a;b", "a`b"]
            common.atomic_json(folder / "task.json", {"deadline": time.time() + 30})
            with patch.dict(os.environ, {"TASK_CONTINUITY_TASK_DIR": td}):
                try:
                    work_tools.run_python(str(script), "calculate", arguments)
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        info = work_tools.job_status(folder / "Jobs/calculate")
                        if info.get("result"):
                            break
                        time.sleep(.05)
                    self.assertEqual(info["result"]["exit_code"], 0, info)
                    self.assertEqual(json.loads((folder / "arguments.json").read_text()), arguments)
                finally:
                    work_tools.stop_task_jobs(folder)

    def test_only_reported_external_outputs_can_be_opened(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as external:
            task = Path(td)
            output = Path(external) / "report.md"
            other = Path(external) / "unrelated.md"
            output.write_text("Reported output")
            other.write_text("Unrelated file")
            common.atomic_json(task / "result.json", {"files": [{"path": str(output)}]})
            self.assertEqual(agent.output_file(task, str(output)), output.resolve())
            with self.assertRaisesRegex(ValueError, "reported outputs"):
                agent.output_file(task, str(other))
            output.unlink()
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                agent.output_file(task, str(output))

    def test_inspecting_script_output_opens_its_folder_without_executing_it(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            script = task / "app.py"
            script.write_text("raise RuntimeError('must never execute')")
            request = handler("/api/open", {"id": "abc", "path": "app.py"})
            with patch.object(agent, "find_task", return_value=task), patch.object(agent.os, "startfile") as start:
                request.post_action()
            start.assert_called_once_with(str(task.resolve()))
            self.assertEqual(request.send.call_args.args[0], 200)

    def test_stop_does_not_relabel_a_finished_task(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            common.atomic_json(task / "task.json", {"status": "completed", "finished_at": 123})
            request = handler("/api/stop", {"id": "abc"})
            with patch.object(agent, "find_task", return_value=task), patch.object(agent, "ACTIVE", None), patch.object(agent, "stop_task_jobs"):
                request.post_action()
            self.assertEqual(common.read_json(task / "task.json"), {"status": "completed", "finished_at": 123})
            self.assertEqual(request.send.call_args.args[0], 200)

    def test_invalid_saved_timestamp_cannot_break_the_task_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for task_id, created in (("a", 123), ("b", "broken timestamp"), ("c", 456)):
                common.atomic_json(root / "Tasks" / task_id / "task.json", {"id": task_id, "created": created})
            with patch.object(agent, "DATA_ROOT", root):
                self.assertEqual([row["id"] for row in agent.list_tasks()], ["c", "a"])

    def test_nontext_task_is_rejected_without_creating_work(self):
        for value in (None, {}, [], 123, True):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "Tasks").mkdir()
                request = handler("/api/start", {"task": value})
                with patch.object(agent, "DATA_ROOT", root), patch.object(agent, "ACTIVE", None), patch.object(agent, "launch_task") as launch:
                    request.post_action()
                self.assertEqual(request.send.call_args.args[0], 400)
                self.assertEqual(list((root / "Tasks").iterdir()), [])
                launch.assert_not_called()

    def test_nontext_followup_is_rejected_without_changing_steering(self):
        request = handler("/api/followup", {"id": "abc", "text": {"not": "an instruction"}})
        with patch.object(agent, "find_task") as lookup:
            request.post_action()
        lookup.assert_not_called()
        self.assertEqual(request.send.call_args.args[0], 400)


if __name__ == "__main__":
    unittest.main()
