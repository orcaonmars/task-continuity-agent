"""Crash the real MCP owner: tracked jobs survive and explicit cancellation wins."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import common  # noqa: E402
import work_tools  # noqa: E402


OWNER = r"""
import asyncio, json, os, sys, time
from pathlib import Path
import psutil
sys.path.insert(0, str(Path(sys.argv[1]) / "app"))
import actor, common
task = Path(sys.argv[2])
common.atomic_json(task / "owner.json", {"pid": os.getpid(), "created": psutil.Process().create_time()})
actor.allow_tracked_job_breakaway()
async def main():
    params = actor.StdioServerParameters(command=str(common.PYTHON), args=[str(common.ROOT / "app/tools.py")], env=dict(os.environ))
    async with actor.stdio_client(params) as (read, write):
        async with actor.ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("shell", {"command": sys.argv[3], "job_name": "mcp-survival", "timeout_seconds": 30})
            if result.isError:
                raise RuntimeError(str(result.content))
            print("RUNNING", flush=True)
            await asyncio.sleep(60)
asyncio.run(main())
"""


@unittest.skipUnless(os.name == "nt", "Windows MCP process ownership")
class McpJobSurvival(unittest.TestCase):
    def test_denied_breakaway_never_falls_back_to_a_coupled_runner(self):
        denied = PermissionError(5, "breakaway denied")
        denied.winerror = 5
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            with patch.object(
                work_tools.subprocess, "Popen", side_effect=denied
            ) as launch:
                with self.assertRaisesRegex(RuntimeError, "no coupled fallback"):
                    work_tools._launch_job_runner(folder)
            launch.assert_called_once()
            self.assertTrue((folder / "launch-intent.json").is_file())
            self.assertFalse((folder / "launcher.json").exists())

    def wait_for(self, predicate, seconds=15):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            result = predicate()
            if result:
                return result
            time.sleep(0.025)
        self.fail("Timed out waiting for real MCP job state")

    def run_case(self, cancel):
        with tempfile.TemporaryDirectory() as td:
            task = Path(td)
            folder = task / "Jobs" / "mcp-survival"
            child = task / "child.py"
            child.write_text(
                "import time\nfrom pathlib import Path\n"
                "Path('child-started.txt').write_text('started')\n"
                "deadline = time.monotonic() + 20\n"
                "while not Path('release-child.txt').exists():\n"
                "    if time.monotonic() >= deadline: raise RuntimeError('test release missing')\n"
                "    time.sleep(0.025)\n"
                "Path('finished.txt').write_text('completed once')\n",
                encoding="utf-8",
            )
            owner = task / "owner.py"
            owner.write_text(OWNER, encoding="utf-8")
            common.atomic_json(task / "task.json", {"deadline": time.time() + 30})
            command = "& '{}' '{}'".format(
                sys.executable.replace("'", "''"), str(child).replace("'", "''")
            )
            env = {
                **os.environ,
                "TASK_CONTINUITY_TASK_DIR": str(task),
                "TASK_CONTINUITY_DATA": str(task / "runtime"),
            }
            launcher = subprocess.Popen(
                [sys.executable, str(owner), str(ROOT), str(task), command],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=common.NO_WINDOW,
            )
            actor_process = None
            try:
                self.wait_for(lambda: (task / "child-started.txt").exists())
                actor_process = work_tools.live_process(
                    common.read_json(task / "owner.json")
                )
                self.assertIsNotNone(actor_process)
                identities = {
                    name: self.wait_for(
                        lambda name=name: common.read_json(folder / name)
                    )
                    for name in ("launcher.json", "runner.json", "process.json")
                }
                runner_before = identities["runner.json"]
                core = [
                    process
                    for process in actor_process.children(recursive=True)
                    if "tools.py" in " ".join(process.cmdline())
                ]
                self.assertTrue(
                    core, "The job must start inside the real core MCP tree"
                )
                actor_process.kill()
                actor_process.wait(timeout=5)
                self.wait_for(lambda: all(not process.is_running() for process in core))
                self.assertIsNotNone(work_tools.live_process(runner_before))
                self.assertFalse((folder / "result.json").exists())
                if cancel:
                    work_tools.stop_task_jobs(task)
                else:
                    (task / "release-child.txt").write_text("continue")
                result = self.wait_for(lambda: common.read_json(folder / "result.json"))
                self.wait_for(
                    lambda: not any(
                        work_tools.live_process(info) for info in identities.values()
                    )
                )
                if cancel:
                    self.assertEqual(result["exit_code"], -1, result)
                    self.assertIn("stop", result.get("error", "").lower())
                    self.assertFalse((task / "finished.txt").exists())
                else:
                    self.assertEqual(result["exit_code"], 0, result)
                    self.assertEqual(
                        (task / "finished.txt").read_text(), "completed once"
                    )
                    self.assertEqual(
                        common.read_json(folder / "runner.json"), runner_before
                    )
            finally:
                work_tools.stop_task_jobs(task)
                if actor_process and actor_process.is_running():
                    actor_process.kill()
                if launcher.poll() is None:
                    launcher.kill()
                launcher.communicate(timeout=10)

    def test_job_survives_real_core_mcp_owner_crash(self):
        self.run_case(cancel=False)

    def test_surviving_job_remains_explicitly_cancellable(self):
        self.run_case(cancel=True)


if __name__ == "__main__":
    unittest.main()
