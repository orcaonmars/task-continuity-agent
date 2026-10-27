"""Run dependency-free UI behavior checks when a modern Node.js is available."""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "UI checks require Node.js 18+; the agent does not")
class WorkspaceUi(unittest.TestCase):
    def test_browser_logic(self):
        version = subprocess.run(
            [NODE, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        if int(version.lstrip("v").split(".")[0]) < 18:
            self.skipTest("UI checks require Node.js 18+")
        result = subprocess.run(
            [NODE, "--test", str(ROOT / "tests/ui.test.cjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
