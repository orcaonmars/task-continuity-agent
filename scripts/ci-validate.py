"""Run the normal build with a non-administrator token on GitHub Actions.

Hosted Windows runners are administrators with UAC disabled:
https://docs.github.com/en/actions/reference/runners/github-hosted-runners#administrative-privileges
CreateRestrictedToken removes privileges and marks Administrators deny-only:
https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-createrestrictedtoken
The runtime's standard_user guard remains unchanged. No accounts or machine
security settings are changed, and failure never falls back to elevated tests.
"""

from __future__ import annotations

import codecs
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
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
    print(f"Starting PowerShell build: {shell}; Python: {sys.executable}.", flush=True)
    result = subprocess.call(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts/build.ps1")],
        cwd=ROOT,
        env=env,
        # CREATE_NO_WINDOW has no console from which to recover default handles.
        # Explicit streams keep PowerShell and every build child on our capture.
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    print(f"PowerShell build exited with code {result}.", flush=True)
    return result


def private_desktop(token, cleanup):
    """Keep the reduced token off an administrator-only service desktop.

    Only newly created objects receive an ACL. The runner's existing window
    station and desktop are restored unchanged before any child is launched.
    """
    import win32api
    import win32con
    import win32security
    import win32service

    def keep(handle, close_method):
        def close():
            try:
                getattr(handle, close_method)()
            finally:
                handle.Detach()

        cleanup.callback(close)
        return handle

    user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    acl = win32security.ACL()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, user)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    attributes = win32security.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    # Do not inherit station/desktop handles: lpDesktop selects the sole intended
    # objects explicitly, avoiding Windows' ambiguous inherited-handle selection.
    attributes.bInheritHandle = False
    previous_station = win32service.GetProcessWindowStation()
    previous_desktop = win32service.GetThreadDesktop(win32api.GetCurrentThreadId())
    try:
        if is_admin():
            station_name = "task-continuity-ci-" + uuid.uuid4().hex
            station = keep(
                win32service.CreateWindowStation(
                    station_name, 1, win32con.GENERIC_ALL, attributes  # CWF_CREATE_ONLY
                ),
                "CloseWindowStation",
            )
            station.SetProcessWindowStation()
        else:
            # Non-admin callers already have an accessible station, and Windows
            # reserves creation of named stations for administrators.
            station_name = win32service.GetUserObjectInformation(previous_station, win32con.UOI_NAME)
        try:
            desktop_name = "continuity-" + uuid.uuid4().hex
            keep(
                win32service.CreateDesktop(desktop_name, 0, win32con.GENERIC_ALL, attributes),
                "CloseDesktop",
            )
        finally:
            previous_station.SetProcessWindowStation()
            current_desktop = win32service.GetThreadDesktop(win32api.GetCurrentThreadId())
            try:
                if int(current_desktop) != int(previous_desktop):
                    previous_desktop.SetThreadDesktop()
            finally:
                current_desktop.Detach()
    finally:
        # GetProcessWindowStation/GetThreadDesktop return borrowed handles.
        previous_desktop.Detach()
        previous_station.Detach()
    return station_name + "\\" + desktop_name


def configure_restricted_dacl(token):
    """Give this token access to its own future objects after Admins is disabled."""
    import win32con
    import win32security

    user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    previous = win32security.GetTokenInformation(token, win32security.TokenDefaultDacl)
    user_grant = previous is None or any(
        ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE
        and ace[-1] == user
        and bool(ace[1] & win32con.GENERIC_ALL)
        for ace in (previous.GetAce(index) for index in range(previous.GetAceCount()))
    )
    print(f"Inherited object defaults include user full access={user_grant}.", flush=True)
    acl = win32security.ACL()
    system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
    for sid in (user, system):
        acl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, sid)
    # This is the newly created restricted token, never the caller's primary token.
    # Its default DACL affects only new objects, including child startup objects.
    win32security.SetTokenInformation(token, win32security.TokenDefaultDacl, acl)


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
                win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY
                | win32con.TOKEN_ASSIGN_PRIMARY | win32con.TOKEN_ADJUST_DEFAULT,
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
        configure_restricted_dacl(restricted)
        startup = win32process.STARTUPINFO()
        startup.dwFlags = win32con.STARTF_USESTDHANDLES
        startup.lpDesktop = private_desktop(token, cleanup)
        input_stream = cleanup.enter_context(open(os.devnull, "rb"))
        output_stream = cleanup.enter_context(tempfile.TemporaryFile(mode="w+b"))
        # Service-runner standard handles may not remain usable after the token
        # transition. Give the child owned file handles and relay its output after
        # it exits. Reading only then also preserves the shared file position.
        for field, source in (
            ("hStdInput", msvcrt.get_osfhandle(input_stream.fileno())),
            ("hStdOutput", msvcrt.get_osfhandle(output_stream.fileno())),
            ("hStdError", msvcrt.get_osfhandle(output_stream.fileno())),
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
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        print("Starting restricted validation worker.", flush=True)
        process, thread, _, _ = win32process.CreateProcessAsUser(
            restricted,
            command[0],
            subprocess.list2cmdline(command),
            None,
            None,
            True,
            win32con.CREATE_NO_WINDOW,
            env,
            str(cwd),
            startup,
        )
        owned(process)
        owned(thread)
        while True:
            status = win32event.WaitForSingleObject(process, 1000)
            if status == win32event.WAIT_OBJECT_0:
                result = win32process.GetExitCodeProcess(process)
                output_stream.seek(0)
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while chunk := output_stream.read(64 * 1024):
                    print(decoder.decode(chunk), end="", flush=True)
                print(decoder.decode(b"", final=True), end="", flush=True)
                print(f"Restricted validation worker exited with code {result}.", flush=True)
                return result
            if status != win32event.WAIT_TIMEOUT:
                raise RuntimeError(f"Waiting for the restricted build failed: {status}")


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("This entry point is only for Windows GitHub Actions.")
    if arguments not in ([], ["--standard-user-build"]):
        raise ValueError("Unexpected CI launcher arguments.")
    administrator = is_admin()
    print(
        f"CI launcher: administrator membership={administrator}; "
        f"restricted worker={bool(arguments)}.",
        flush=True,
    )
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
