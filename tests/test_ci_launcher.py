"""CI privilege reduction must preserve the normal runtime guard and build result."""

import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, Mock, patch

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
        self.assertIs(kwargs["stdout"], sys.stdout)
        self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)


class RestrictedToken(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Native Windows token and PowerShell")
    def test_nested_powershell_build_forwards_both_channels_and_failure_status(self):
        with tempfile.TemporaryDirectory(prefix="continuity-ci-build-") as folder:
            temporary = Path(folder)
            (temporary / "scripts").mkdir()
            (temporary / "scripts/build.ps1").write_text(
                "Write-Output 'Nested build stdout'\n"
                "[Console]::Error.WriteLine('Nested build stderr')\nexit 17\n", encoding="utf-8",
            )
            command = [
                sys.executable, "-c",
                "import importlib.util,sys;from pathlib import Path;"
                f"spec=importlib.util.spec_from_file_location('ci_native',{str(ROOT / 'scripts/ci-validate.py')!r});"
                "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);"
                f"module.ROOT=Path({str(temporary)!r});sys.exit(module.build())",
            ]
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(launcher.run_restricted(command, ROOT), 17)
            self.assertIn("Nested build stdout", output.getvalue())
            self.assertIn("Nested build stderr", output.getvalue())
            self.assertIn("PowerShell build exited with code 17.", output.getvalue())

    @unittest.skipUnless(os.name == "nt", "Native Windows token and process APIs")
    def test_admin_only_object_defaults_fail_until_new_restricted_token_is_repaired(self):
        import win32api
        import win32con
        import win32security

        source = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY
        )
        try:
            original = win32security.GetTokenInformation(source, win32security.TokenDefaultDacl)
            original_aces = [original.GetAce(index) for index in range(original.GetAceCount())]
            for repair in (False, True):
                # Simulate elevated/service object defaults using a private copy;
                # the test never modifies the current process's primary token.
                duplicate = win32security.DuplicateTokenEx(
                    source, win32security.SecurityImpersonation,
                    win32con.TOKEN_ALL_ACCESS, win32security.TokenPrimary,
                )
                acl = win32security.ACL()
                for kind in (win32security.WinBuiltinAdministratorsSid, win32security.WinLocalSystemSid):
                    acl.AddAccessAllowedAce(
                        win32security.ACL_REVISION, win32con.GENERIC_ALL,
                        win32security.CreateWellKnownSid(kind, None),
                    )
                win32security.SetTokenInformation(duplicate, win32security.TokenDefaultDacl, acl)
                output = io.StringIO()
                with (
                    patch.object(win32security, "OpenProcessToken", return_value=duplicate),
                    nullcontext() if repair else patch.object(launcher, "configure_restricted_dacl"),
                    redirect_stdout(output),
                ):
                    result = launcher.run_restricted(
                        [sys.executable, "-c", "import ctypes,sys;"
                         "print('Repaired child administrator=',bool(ctypes.windll.shell32.IsUserAnAdmin()));"
                         "sys.exit(23)"], ROOT,
                    )
                if repair:
                    self.assertEqual(result, 23)
                    self.assertIn("user full access=False", output.getvalue())
                    self.assertIn("Repaired child administrator= False", output.getvalue())
                else:
                    self.assertEqual(result & 0xffffffff, 0xC0000142)
                    self.assertNotIn("Repaired child administrator=", output.getvalue())
            unchanged = win32security.GetTokenInformation(source, win32security.TokenDefaultDacl)
            self.assertEqual(
                [unchanged.GetAce(index) for index in range(unchanged.GetAceCount())], original_aces
            )
        finally:
            source.Close()

    @unittest.skipUnless(os.name == "nt", "Native Windows token and process APIs")
    def test_real_restricted_child_is_non_admin_and_preserves_failure_exit_code(self):
        import win32api
        import win32service

        command = [
            sys.executable,
            "-c",
            "import ctypes,sys;"
            "administrator=bool(ctypes.windll.shell32.IsUserAnAdmin());"
            "print('Native CI token smoke: administrator=',administrator,flush=True);"
            "print('Captured Unicode: \\u2713',flush=True);"
            "print('Captured stderr',file=sys.stderr,flush=True);"
            "import win32api,win32con,win32service;"
            "desktop=win32service.GetThreadDesktop(win32api.GetCurrentThreadId());"
            "print('Native desktop:',win32service.GetUserObjectInformation(desktop,win32con.UOI_NAME));"
            "desktop.Detach();"
            "sys.exit(91 if administrator else 19)",
        ]
        output = io.StringIO()
        # A parent stream without a Windows file handle models the hosted runner's
        # service output. The child must use its own capture for both channels.
        previous_station = win32service.GetProcessWindowStation()
        previous_desktop = win32service.GetThreadDesktop(win32api.GetCurrentThreadId())
        try:
            with redirect_stdout(output):
                self.assertEqual(launcher.run_restricted(command, ROOT), 19)
            current_station = win32service.GetProcessWindowStation()
            current_desktop = win32service.GetThreadDesktop(win32api.GetCurrentThreadId())
            try:
                self.assertEqual(int(current_station), int(previous_station))
                self.assertEqual(int(current_desktop), int(previous_desktop))
            finally:
                current_station.Detach()
                current_desktop.Detach()
        finally:
            previous_station.Detach()
            previous_desktop.Detach()
        captured = output.getvalue()
        self.assertIn("Starting restricted validation worker.", captured)
        self.assertIn("administrator= False", captured)
        self.assertIn("Captured Unicode: \u2713", captured)
        self.assertIn("Captured stderr", captured)
        self.assertIn("Native desktop: continuity-", captured)
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
            TOKEN_ADJUST_DEFAULT=128,
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
        with (
            patch.dict(sys.modules, fixture.modules),
            patch.object(launcher, "private_desktop", return_value="owned-station\\owned-desktop"),
            patch.object(launcher, "configure_restricted_dacl") as configure_dacl,
        ):
            self.assertEqual(launcher.run_restricted(command, ROOT), 23)
        fixture.security.OpenProcessToken.assert_called_once_with("current-process", 139)
        fixture.security.CreateRestrictedToken.assert_called_once_with(
            fixture.token, 1, [("administrators-sid", 0)], None, None
        )
        configure_dacl.assert_called_once_with(fixture.restricted)
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
        self.assertEqual(fixture.startup.lpDesktop, "owned-station\\owned-desktop")
        self.assertEqual(fixture.startup.hStdOutput, fixture.streams[1])
        self.assertEqual(fixture.startup.hStdError, fixture.streams[2])
        for handle in [fixture.token, fixture.restricted, fixture.process, fixture.thread, *fixture.streams]:
            handle.Close.assert_called_once_with()

    def test_spawn_failure_closes_tokens_and_logs_without_an_elevated_retry(self):
        fixture = self.api(OSError("restricted process launch denied"))
        with (
            patch.dict(sys.modules, fixture.modules),
            patch.object(launcher, "private_desktop", return_value="owned-station\\owned-desktop"),
            patch.object(launcher, "configure_restricted_dacl"),
            patch.object(launcher, "build") as build,
            self.assertRaisesRegex(OSError, "launch denied"),
        ):
            launcher.run_restricted(["python.exe"], ROOT)
        fixture.processes.CreateProcessAsUser.assert_called_once()
        build.assert_not_called()
        for handle in [fixture.token, fixture.restricted, *fixture.streams]:
            handle.Close.assert_called_once_with()


class PrivateDesktop(unittest.TestCase):
    def api(self, error=None):
        previous_station, station, desktop = [Mock() for _ in range(3)]
        previous_desktop, current_desktop = MagicMock(), MagicMock()
        previous_desktop.__int__.return_value = 100
        current_desktop.__int__.return_value = 101
        service = Mock()
        service.GetProcessWindowStation.return_value = previous_station
        service.GetThreadDesktop.side_effect = [previous_desktop, current_desktop]
        service.CreateWindowStation.return_value = station
        service.CreateDesktop.return_value = desktop
        service.GetUserObjectInformation.return_value = "accessible-station"
        if error:
            service.CreateDesktop.side_effect = error
        security = Mock()
        security.GetTokenInformation.return_value = ("current-user-sid", 0)
        attributes = SimpleNamespace()
        security.SECURITY_ATTRIBUTES.return_value = attributes
        constants = SimpleNamespace(GENERIC_ALL=0x10000000, UOI_NAME=2)
        modules = {"win32api": Mock(), "win32con": constants,
                   "win32security": security, "win32service": service}
        return SimpleNamespace(**locals())

    def test_owned_station_and_desktop_only_allow_current_user_and_live_until_cleanup(self):
        fixture = self.api()
        with (
            patch.dict(sys.modules, fixture.modules),
            patch.object(launcher, "is_admin", return_value=True),
            patch.object(launcher.uuid, "uuid4", return_value=SimpleNamespace(hex="unique")),
        ):
            with ExitStack() as cleanup:
                self.assertEqual(
                    launcher.private_desktop("own-token", cleanup),
                    "task-continuity-ci-unique\\continuity-unique",
                )
                fixture.station.CloseWindowStation.assert_not_called()
                fixture.desktop.CloseDesktop.assert_not_called()
                fixture.previous_station.SetProcessWindowStation.assert_called_once_with()
                fixture.previous_desktop.SetThreadDesktop.assert_called_once_with()
            fixture.station.CloseWindowStation.assert_called_once_with()
            fixture.desktop.CloseDesktop.assert_called_once_with()
        fixture.security.ACL.return_value.AddAccessAllowedAce.assert_called_once_with(
            fixture.security.ACL_REVISION, 0x10000000, "current-user-sid"
        )
        fixture.service.CreateWindowStation.assert_called_once_with(
            "task-continuity-ci-unique", 1, 0x10000000, fixture.attributes
        )
        fixture.service.CreateDesktop.assert_called_once_with(
            "continuity-unique", 0, 0x10000000, fixture.attributes
        )
        self.assertFalse(fixture.attributes.bInheritHandle)
        fixture.previous_station.CloseWindowStation.assert_not_called()
        fixture.previous_desktop.CloseDesktop.assert_not_called()
        for handle in (fixture.station, fixture.desktop, fixture.previous_station,
                       fixture.previous_desktop, fixture.current_desktop):
            handle.Detach.assert_called_once_with()

    def test_desktop_creation_error_restores_parent_and_closes_only_owned_station(self):
        fixture = self.api(OSError("private desktop creation failed"))
        with (
            patch.dict(sys.modules, fixture.modules),
            patch.object(launcher, "is_admin", return_value=True),
            self.assertRaisesRegex(OSError, "private desktop creation failed"),
            ExitStack() as cleanup,
        ):
            launcher.private_desktop("own-token", cleanup)
        fixture.previous_station.SetProcessWindowStation.assert_called_once_with()
        fixture.previous_desktop.SetThreadDesktop.assert_called_once_with()
        fixture.station.CloseWindowStation.assert_called_once_with()
        fixture.previous_station.CloseWindowStation.assert_not_called()
        fixture.previous_desktop.CloseDesktop.assert_not_called()
        fixture.desktop.CloseDesktop.assert_not_called()

    def test_non_admin_uses_new_desktop_on_existing_accessible_station(self):
        fixture = self.api()
        with (
            patch.dict(sys.modules, fixture.modules),
            patch.object(launcher, "is_admin", return_value=False),
            patch.object(launcher.uuid, "uuid4", return_value=SimpleNamespace(hex="unique")),
            ExitStack() as cleanup,
        ):
            self.assertEqual(
                launcher.private_desktop("own-token", cleanup),
                "accessible-station\\continuity-unique",
            )
        fixture.service.CreateWindowStation.assert_not_called()
        fixture.desktop.CloseDesktop.assert_called_once_with()
        fixture.previous_station.CloseWindowStation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
