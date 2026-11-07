"""First-run installation decisions and connection settings regressions."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import agent
import common


class ConnectionSetup(unittest.TestCase):
    def test_connection_form_receives_only_the_editable_fields(self):
        cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
        cfg["extra_mcp_servers"] = {"private": {"command":"server", "env":{"TOKEN":"do-not-expose"}}}
        with patch.object(agent, "settings", return_value=cfg):
            result = agent.connection_settings()
        self.assertEqual(set(result), {"provider", "api_base", "preferred_model"})
        self.assertNotIn("do-not-expose", json.dumps(result))

    def test_saving_connection_preserves_other_settings(self):
        cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
        cfg["max_output_tokens"] = 8192
        cfg["capabilities"]["web"] = False
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            with patch.object(agent, "settings", return_value=cfg), patch.object(agent, "CONFIG", path), patch.object(agent, "ACTIVE", None):
                result = agent.save_connection({"provider":"openai-compatible", "api_base":"http://127.0.0.1:11434/v1/", "preferred_model":" example "})
            saved = common.read_json(path)
            self.assertEqual(result["api_base"], "http://127.0.0.1:11434/v1")
            self.assertEqual(saved["preferred_model"], "example")
            self.assertEqual(saved["max_output_tokens"], 8192)
            self.assertFalse(saved["capabilities"]["web"])

    def test_invalid_or_active_connection_change_does_not_write(self):
        valid = {"provider":"lmstudio", "api_base":"http://127.0.0.1:1234/v1", "preferred_model":""}
        for changes in ({"api_base":"not a URL"}, {"provider":"unknown"}, {"preferred_model":None}, {"capabilities":{}}):
            with self.subTest(changes=changes), patch.object(agent, "ACTIVE", None), patch.object(agent, "settings", return_value=copy.deepcopy(common.DEFAULT_SETTINGS)), patch.object(agent, "atomic_json") as write:
                with self.assertRaises(ValueError):
                    agent.save_connection({**valid, **changes})
                write.assert_not_called()
        with patch.object(agent, "ACTIVE", Path("active")), patch.object(agent, "atomic_json") as write:
            with self.assertRaisesRegex(ValueError, "current task"):
                agent.save_connection(valid)
            write.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows first-run setup")
class WindowsSetup(unittest.TestCase):
    def test_double_click_wrapper_handles_spaces_and_ampersands(self):
        with tempfile.TemporaryDirectory(prefix="setup & spaces ") as td:
            folder = Path(td)
            launcher = folder / "Start Task Continuity.cmd"
            launcher.write_bytes((ROOT / launcher.name).read_bytes())
            (folder / "setup.ps1").write_text("[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'wrapper-ran.txt'), 'started')", encoding="utf-8")
            result = subprocess.run(["cmd.exe", "/d", "/c", launcher.name], cwd=folder, capture_output=True,
                                    encoding="utf-8", errors="replace", timeout=15,
                                    creationflags=0x08000000)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((folder / "wrapper-ran.txt").read_text(), "started")

    def powershell(self, source, folder):
        environment = {key:value for key,value in os.environ.items() if not key.startswith("TASK_CONTINUITY")}
        environment.update(SETUP_SOURCE=str(ROOT / "setup.ps1"), SETUP_FIXTURE=folder, SETUP_PYTHON=sys.executable)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "$ErrorActionPreference='Stop'; . $env:SETUP_SOURCE; " + source],
            env=environment, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_python_probe_uses_real_runtime_and_skips_store_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            self.powershell(r'''
if ((Test-SetupPython $env:SETUP_PYTHON) -ne $env:SETUP_PYTHON) { throw 'Runtime probe failed.' }
if (Test-SetupPython 'C:\fake\Microsoft\WindowsApps\python.exe') { throw 'Store alias was accepted.' }
''', td)

    def test_winget_is_exact_per_user_and_failure_never_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self.powershell(r'''
function Fake-Winget { $script:captured=@($args); $global:LASTEXITCODE=0; if($script:fail){$global:LASTEXITCODE=123} }
function Get-Command { param($Name,$ErrorAction); if($Name -ne 'winget'){throw 'Unexpected command'}; return [pscustomobject]@{Source='Fake-Winget'} }
Invoke-SetupWinget 'Python.Python.3.12' (Join-Path $env:SETUP_FIXTURE 'winget.log')
if (($script:captured -join '|') -notmatch '\-\-scope\|user') { throw 'Per-user scope missing.' }
if ($script:captured -contains 'machine' -or $script:captured -notcontains '--exact' -or $script:captured -notcontains '--disable-interactivity') { throw 'Unexpected installer policy.' }
$script:fail=$true
$caught=$false
try { Invoke-SetupWinget 'Python.Python.3.12' (Join-Path $env:SETUP_FIXTURE 'failed.log') } catch { $caught=$_.Exception.Message -match 'No administrator fallback' }
if(!$caught){throw 'Installer failure did not remain a failure.'}
''', td)

    def test_first_run_bootstraps_python_then_marks_success(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "requirements.txt").write_text("example==1\n")
            self.powershell(r'''
function Assert-SetupUser {}
function Test-SetupReady { return $false }
$script:finds=0
function Find-SetupPython { $script:finds++; if($script:finds -gt 1){return $env:SETUP_PYTHON} }
function Invoke-SetupWinget { param($PackageId,$LogPath); if($PackageId -ne 'Python.Python.3.12'){throw 'Unexpected package'}; $script:installedPython=$true }
function Invoke-SetupInstall { param($Root,$Python,$LogPath,[switch]$NoShortcut); if(!$script:installedPython -or !$NoShortcut){throw 'Wrong install order or shortcut option'}; $script:installedApp=$true; $global:LASTEXITCODE=0 }
function Find-SetupLMStudio { return $null }
Invoke-TaskSetup -Root $env:SETUP_FIXTURE -NoLaunch -NoShortcut
if(!$script:installedApp -or !(Test-Path (Join-Path $env:SETUP_FIXTURE 'data\State\setup-complete.json'))){throw 'Setup did not finish.'}
''', td)

    def test_ready_installation_skips_downloads_and_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "data").mkdir()
            (folder / "data/config.json").write_text('{"provider":"openai-compatible"}')
            (folder / "data/keep.txt").write_text("saved work")
            self.powershell(r'''
function Assert-SetupUser {}
function Test-SetupReady { return $true }
function Invoke-SetupWinget { throw 'Must not download again.' }
function Invoke-SetupInstall { throw 'Must not reinstall.' }
function Find-SetupLMStudio { return $null }
Invoke-TaskSetup -Root $env:SETUP_FIXTURE -NoLaunch -NoShortcut
''', td)
            self.assertEqual((folder / "data/keep.txt").read_text(), "saved work")
            self.assertEqual(json.loads((folder / "data/config.json").read_text()), {"provider":"openai-compatible"})

    def test_failed_application_install_is_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "requirements.txt").write_text("example==1\n")
            self.powershell(r'''
function Assert-SetupUser {}
function Test-SetupReady { return $false }
function Find-SetupPython { return $env:SETUP_PYTHON }
function Invoke-SetupInstall { throw 'Deliberate installation failure' }
$caught=$false
try { Invoke-TaskSetup -Root $env:SETUP_FIXTURE -NoLaunch -NoShortcut } catch { $caught=$_.Exception.Message -match 'Deliberate installation failure' }
if(!$caught -or (Test-Path (Join-Path $env:SETUP_FIXTURE 'data\State\setup-complete.json'))){throw 'Failed setup was marked successful.'}
''', td)


if __name__ == "__main__":
    unittest.main()
