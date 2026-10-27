"""Run the normal build with a non-administrator token on GitHub Actions.

Hosted Windows runners are administrators with UAC disabled:
https://docs.github.com/en/actions/reference/runners/github-hosted-runners#administrative-privileges
CreateRestrictedToken removes privileges and marks Administrators deny-only:
https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-createrestrictedtoken
The runtime's standard_user guard remains unchanged. No accounts or machine
security settings are changed, and failure never falls back to elevated tests.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def build():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("PowerShell is required for the Windows build.")
    env = dict(os.environ)
    env["TASK_CONTINUITY_TEST_PYTHON"] = sys.executable
    return subprocess.call(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts/build.ps1")],
        cwd=ROOT,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def run_restricted(command, cwd):
    """Launch using a restricted copy of our own primary token, retaining CI logs."""
    import msvcrt
    import win32api
    import win32con
    import win32event
    import win32process
    import win32security

    with ExitStack() as cleanup:

        def owned(handle):
            cleanup.callback(handle.Close)
            return handle

        current = win32api.GetCurrentProcess()
        token = owned(
            win32security.OpenProcessToken(
                current,
                win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY | win32con.TOKEN_ASSIGN_PRIMARY,
            )
        )
        administrators = win32security.CreateWellKnownSid(
            win32security.WinBuiltinAdministratorsSid, None
        )
        restricted = owned(
            win32security.CreateRestrictedToken(
                token,
                win32security.DISABLE_MAX_PRIVILEGE,
                [(administrators, 0)],
                None,
                None,
            )
        )
        startup = win32process.STARTUPINFO()
        startup.dwFlags = win32con.STARTF_USESTDHANDLES
        input_stream = cleanup.enter_context(open(os.devnull, "rb"))
        # GitHub captures output through pipes. Duplicate only the standard handles
        # needed by this child, without changing the parent's inheritance settings.
        for field, source in (
            ("hStdInput", msvcrt.get_osfhandle(input_stream.fileno())),
            ("hStdOutput", win32api.GetStdHandle(win32api.STD_OUTPUT_HANDLE)),
            ("hStdError", win32api.GetStdHandle(win32api.STD_ERROR_HANDLE)),
        ):
            handle = owned(
                win32api.DuplicateHandle(
                    current,
                    source,
                    current,
                    0,
                    True,
                    win32con.DUPLICATE_SAME_ACCESS,
                )
            )
            setattr(startup, field, handle)
        process, thread, _, _ = win32process.CreateProcessAsUser(
            restricted,
            command[0],
            subprocess.list2cmdline(command),
            None,
            None,
            True,
            win32con.CREATE_NO_WINDOW,
            None,
            str(cwd),
            startup,
        )
        owned(process)
        owned(thread)
        while True:
            status = win32event.WaitForSingleObject(process, 1000)
            if status == win32event.WAIT_OBJECT_0:
                return win32process.GetExitCodeProcess(process)
            if status != win32event.WAIT_TIMEOUT:
                raise RuntimeError(f"Waiting for the restricted build failed: {status}")


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("This entry point is only for Windows GitHub Actions.")
    if arguments not in ([], ["--standard-user-build"]):
        raise ValueError("Unexpected CI launcher arguments.")
    administrator = is_admin()
    if arguments == ["--standard-user-build"]:
        if administrator:
            raise RuntimeError("Refusing to run the build as an administrator.")
        print("Running validation and packaging without administrator membership.", flush=True)
        return build()
    if not administrator:
        return build()
    return run_restricted(
        [sys.executable, str(Path(__file__).resolve()), "--standard-user-build"], ROOT
    )


if __name__ == "__main__":
    raise SystemExit(main())
