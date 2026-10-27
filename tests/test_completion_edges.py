"""Completion checks each reported output, including inferred file types."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import acceptance


class ReportedFiles(unittest.TestCase):
    def report(self, *names):
        return {
            "status": "completed",
            "summary": "Saved outputs",
            "verification": "Checked outputs",
            "files": [{"path": name} for name in names],
        }

    def test_valid_json_does_not_hide_invalid_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(
                task, "Create two JSON files, first.json and second.json"
            )
            (task / "first.json").write_text('{"ok":true}')
            (task / "second.json").write_text("{")
            with self.assertRaisesRegex(ValueError, "second.json"):
                acceptance.validate_completion(
                    task, self.report("first.json", "second.json")
                )

    def test_inferred_json_still_requires_valid_structure(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(
                task, "Create the application configuration file"
            )
            (task / "config.json").write_text("{")
            with self.assertRaisesRegex(ValueError, "config.json"):
                acceptance.validate_completion(task, self.report("config.json"))

    def test_gltf_array_is_rejected_with_structural_error(self):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            acceptance.initialize_contract(task, "Export scene.gltf")
            (task / "scene.gltf").write_text("[]")
            # Avoid visual-review prerequisites to isolate format parsing.
            with self.assertRaisesRegex(ValueError, "asset metadata"):
                acceptance._verify_file_format(task / "scene.gltf", ".gltf")

    def test_text_verification_streams_and_catches_truncated_utf8(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "large.txt"
            path.write_bytes(b"x" * (2 * 1024 * 1024) + b"\xe2\x82\xac")
            with patch.object(
                Path, "read_text", side_effect=AssertionError("unbounded text read")
            ):
                acceptance._verify_file_format(path, ".txt")
                path.write_bytes(b"text\xe2\x82")
                with self.assertRaises(UnicodeError):
                    acceptance._verify_file_format(path, ".txt")


if __name__ == "__main__":
    unittest.main()
