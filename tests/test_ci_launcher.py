"""CI privilege reduction must preserve the normal runtime guard and build result."""

import importlib.util
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_validate", ROOT / "scripts/ci-validate.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class CIEntryPoint(unittest.TestCase):
    def test_outside_github_actions_refuses_before_starting_any_process(self):
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}),
            patch.object(launcher, "build") as build,
            patch.object(launcher, "run_restricted") as restricted,
            self.assertRaisesRegex(RuntimeError, "only for Windows GitHub Actions"),
        ):
            launcher.main([])
        build.assert_not_called()
        restricted.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows CI entry point")
    def test_normal_user_build_retains_exit_code(self):
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
            patch.object(launcher, "is_admin", return_value=False),
            patch.object(launcher, "build", return_value=17) as build,
            patch.object(launcher, "run_restricted") as restricted,
        ):
            self.assertEqual(launcher.main([]), 17)
        build.assert_called_once_with()
        restricted.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows CI entry point")
    def test_admin_launches_only_fixed_guarded_worker_and_propagates_result(self):
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
            patch.object(launcher, "is_admin", return_value=True),
            patch.object(launcher, "build") as build,
            patch.object(launcher, "run_restricted", return_value=5) as restricted,
        ):
            self.assertEqual(launcher.main([]), 5)
        build.assert_not_called()
        restricted.assert_called_once_with(
            [sys.executable, str(ROOT / "scripts/ci-validate.py"), "--standard-user-build"],
            ROOT,
        )

    @unittest.skipUnless(os.name == "nt", "Windows CI entry point")
    def test_worker_rechecks_non_admin_membership_without_a_fallback(self):
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
            patch.object(launcher, "is_admin", return_value=True),
            patch.object(launcher, "build") as build,
            self.assertRaisesRegex(RuntimeError, "Refusing"),
        ):
            launcher.main(["--standard-user-build"])
        build.assert_not_called()

    def test_build_uses_exact_current_python_and_existing_build_script(self):
        with (
            patch.object(launcher.shutil, "which", return_value="C:/PowerShell/pwsh.exe"),
            patch.object(launcher.subprocess, "call", return_value=9) as call,
        ):
            self.assertEqual(launcher.build(), 9)
        args, kwargs = call.call_args
        self.assertEqual(args[0][-1], str(ROOT / "scripts/build.ps1"))
        self.assertEqual(kwargs["cwd"], ROOT)
        self.assertEqual(kwargs["env"]["TASK_CONTINUITY_TEST_PYTHON"], sys.executable)


class RestrictedToken(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Native Windows token and process APIs")
    def test_real_restricted_child_is_non_admin_and_preserves_failure_exit_code(self):
        command = [
            sys.executable,
            "-c",
            "import ctypes,sys;"
            "administrator=bool(ctypes.windll.shell32.IsUserAnAdmin());"
            "print('Native CI token smoke: administrator=',administrator,flush=True);"
            "print('Captured Unicode: \\u2713',flush=True);"
            "print('Captured stderr',file=sys.stderr,flush=True);"
            "sys.exit(91 if administrator else 19)",
        ]
        output = io.StringIO()
        # A parent stream without a Windows file handle models the hosted runner's
        # service output. The child must use its own capture for both channels.
        with redirect_stdout(output):
            self.assertEqual(launcher.run_restricted(command, ROOT), 19)
        captured = output.getvalue()
        self.assertIn("Starting restricted validation worker.", captured)
        self.assertIn("administrator= False", captured)
        self.assertIn("Captured Unicode: \u2713", captured)
        self.assertIn("Captured stderr", captured)
        self.assertIn("Restricted validation worker exited with code 19.", captured)

    @unittest.skipUnless(os.name == "nt", "Native Windows token and process APIs")
    def test_child_traceback_and_split_utf8_are_visible_without_parent_file_handles(self):
        command = [
            sys.executable,
            "-c",
            "import sys;"
            "sys.stdout.write('x'*65535+'\\u2713\\n');sys.stdout.flush();"
            "raise RuntimeError('Restricted child startup diagnostic')",
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(launcher.run_restricted(command, ROOT), 1)
        captured = output.getvalue()
        self.assertIn("x" * 65535 + "\u2713\n", captured.replace("\r\n", "\n"))
        self.assertNotIn("\ufffd", captured)
        self.assertIn("RuntimeError: Restricted child startup diagnostic", captured)
        self.assertIn("Restricted validation worker exited with code 1.", captured)

    def api(self, error=None):
        token, restricted, process, thread = [Mock() for _ in range(4)]
        streams = [Mock(), Mock(), Mock()]
        startup = SimpleNamespace()
        api = Mock()
        api.STD_INPUT_HANDLE = -10
        api.STD_OUTPUT_HANDLE = -11
        api.STD_ERROR_HANDLE = -12
        api.GetCurrentProcess.return_value = "current-process"
        api.GetStdHandle.side_effect = [10, 11, 12]
        api.DuplicateHandle.side_effect = streams
        security = Mock()
        security.OpenProcessToken.return_value = token
        security.CreateWellKnownSid.return_value = "administrators-sid"
        security.CreateRestrictedToken.return_value = restricted
        security.DISABLE_MAX_PRIVILEGE = 1
        security.WinBuiltinAdministratorsSid = 26
        constants = SimpleNamespace(
            TOKEN_DUPLICATE=2,
            TOKEN_QUERY=8,
            TOKEN_ASSIGN_PRIMARY=1,
            STARTF_USESTDHANDLES=256,
            DUPLICATE_SAME_ACCESS=2,
            CREATE_NO_WINDOW=0x08000000,
        )
        processes = Mock()
        processes.STARTUPINFO.return_value = startup
        processes.CreateProcessAsUser.return_value = (process, thread, 100, 101)
        if error:
            processes.CreateProcessAsUser.side_effect = error
        processes.GetExitCodeProcess.return_value = 23
        events = Mock(WAIT_OBJECT_0=0, WAIT_TIMEOUT=258)
        events.WaitForSingleObject.side_effect = [258, 0]
        modules = {
            "win32api": api,
            "win32con": constants,
            "win32event": events,
            "win32process": processes,
            "win32security": security,
        }
        return SimpleNamespace(**locals())

    def test_reduces_own_token_preserves_logs_and_closes_every_handle(self):
        fixture = self.api()
        command = ["C:/Python/python.exe", "C:/project with spaces/worker.py"]
        with patch.dict(sys.modules, fixture.modules):
            self.assertEqual(launcher.run_restricted(command, ROOT), 23)
        fixture.security.OpenProcessToken.assert_called_once_with("current-process", 11)
        fixture.security.CreateRestrictedToken.assert_called_once_with(
            fixture.token, 1, [("administrators-sid", 0)], None, None
        )
        fixture.processes.CreateProcessAsUser.assert_called_once_with(
            fixture.restricted,
            command[0],
            subprocess.list2cmdline(command),
            None,
            None,
            True,
            0x08000000,
            ANY,
            str(ROOT),
            fixture.startup,
        )
        child_env = fixture.processes.CreateProcessAsUser.call_args.args[7]
        self.assertEqual(child_env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(child_env["PYTHONUNBUFFERED"], "1")
        fixture.api.GetStdHandle.assert_not_called()
        # stdout and stderr duplicate the same owned capture file; stdin has a
        # separate null input. No service-runner handle is inherited.
        duplicated_sources = [call.args[1] for call in fixture.api.DuplicateHandle.call_args_list]
        self.assertEqual(duplicated_sources[1], duplicated_sources[2])
        self.assertNotEqual(duplicated_sources[0], duplicated_sources[1])
        self.assertEqual(fixture.startup.hStdInput, fixture.streams[0])
        self.assertEqual(fixture.startup.hStdOutput, fixture.streams[1])
        self.assertEqual(fixture.startup.hStdError, fixture.streams[2])
        for handle in [fixture.token, fixture.restricted, fixture.process, fixture.thread, *fixture.streams]:
            handle.Close.assert_called_once_with()

    def test_spawn_failure_closes_tokens_and_logs_without_an_elevated_retry(self):
        fixture = self.api(OSError("restricted process launch denied"))
        with (
            patch.dict(sys.modules, fixture.modules),
            patch.object(launcher, "build") as build,
            self.assertRaisesRegex(OSError, "launch denied"),
        ):
            launcher.run_restricted(["python.exe"], ROOT)
        fixture.processes.CreateProcessAsUser.assert_called_once()
        build.assert_not_called()
        for handle in [fixture.token, fixture.restricted, *fixture.streams]:
            handle.Close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
