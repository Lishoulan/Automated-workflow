"""Exercise supervisor ownership and recovery using a harmless local fake runner."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "scripts/wechat_runner_supervisor.ps1"
REGISTRAR = ROOT / "scripts/register_wechat_runner.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Task Scheduler and PowerShell integration")


@pytest.fixture
def fake_runner(tmp_path):
    root = tmp_path / "runner with spaces"
    (root / "bin").mkdir(parents=True)
    (root / "bin/Runner.Listener.exe").touch()
    (root / "run.cmd").write_text("@echo off\nexit /b 0\n", encoding="ascii")
    return root


def args(path, root, *flags):
    return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(path), "-RunnerRoot", str(root), "-PythonPath", sys.executable, *flags]


def test_check_only_never_starts_runner_or_writes_state(fake_runner):
    result = subprocess.run(args(SUPERVISOR, fake_runner, "-CheckOnly"), capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(result.stdout)["status"] == "stopped"
    assert not (fake_runner / "supervision").exists()


def test_existing_worker_is_preserved_and_unrelated_runner_is_not_owned(fake_runner):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = "function global:Get-CimInstance { [pscustomobject]@{ExecutablePath=" + quote(fake_runner / "bin/Runner.Worker.exe") + ";ProcessId=123}; [pscustomobject]@{ExecutablePath='C:\\different-runner\\bin\\Runner.Listener.exe';ProcessId=124} }; & "
    script += quote(SUPERVISOR) + " -RunnerRoot " + quote(fake_runner) + " -PythonPath " + quote(sys.executable) + " -Once"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=20, check=True)
    row = json.loads(result.stdout)
    assert row["status"] == "existing_process" and row["owned_processes"] == 1
    assert not (fake_runner / "supervision/runner.log").exists()


def test_exit_zero_is_recovered_and_second_supervisor_cannot_start_duplicate(fake_runner):
    (fake_runner / "run.cmd").write_text("""@echo off
if exist starts.txt (set /p COUNT=<starts.txt) else (set COUNT=0)
set /a COUNT+=1 >nul
>starts.txt echo %COUNT%
ping 127.0.0.1 -n 4 >nul
if %COUNT% GEQ 2 echo paused>supervision\\paused
exit /b 0
""", encoding="ascii")
    first = subprocess.Popen(args(SUPERVISOR, fake_runner, "-RetrySeconds", "5"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while not (fake_runner / "starts.txt").exists():
            assert first.poll() is None
            if time.monotonic() > deadline:
                pytest.fail("Fake runner failed to start")
            time.sleep(.1)
        second = subprocess.run(args(SUPERVISOR, fake_runner, "-Once"), capture_output=True, text=True, timeout=20, check=True)
        assert json.loads(second.stdout)["status"] == "already_supervised"
        output, errors = first.communicate(timeout=30)
        assert first.returncode == 0, errors
        assert (fake_runner / "starts.txt").read_text().strip() == "2"
        assert json.loads(output.splitlines()[-1])["status"] == "paused"
        # The lock is released, and maintenance does not restart the runner.
        paused = subprocess.run(args(SUPERVISOR, fake_runner, "-Once"), capture_output=True, text=True, timeout=20, check=True)
        assert json.loads(paused.stdout)["status"] == "paused"
        assert (fake_runner / "starts.txt").read_text().strip() == "2"
    finally:
        if first.poll() is None:
            (fake_runner / "supervision/paused").touch()
            first.communicate(timeout=30)


def test_registration_preview_does_not_create_tasks_or_change_network(fake_runner):
    result = subprocess.run(args(REGISTRAR, fake_runner, "-DryRun"), capture_output=True, text=True, timeout=20, check=True)
    row = json.loads(result.stdout)
    assert row["logon"] == "Interactive" and row["start_on_logon"] is True
    assert row["publication_action"] is False and row["repeat_minutes"] == 2
    assert "-WindowStyle Hidden" in row["arguments"]
    assert not (fake_runner / "supervision").exists()
    code = SUPERVISOR.read_text(encoding="utf-8") + REGISTRAR.read_text(encoding="utf-8")
    assert "Stop-Process" not in code and "HTTP_PROXY" not in code and "netsh" not in code
    assert "IgnoreNew" in code and "ExecutionTimeLimit ([TimeSpan]::Zero)" in code
    assert "GetFolderPath('Startup')" in code and "0x80070005" in code
    assert "Refusing to overwrite an unrelated Startup entry" in code
    assert "-WindowStyle Hidden" in code


def test_relative_root_is_refused_before_any_process_or_state_change(tmp_path):
    result = subprocess.run(args(SUPERVISOR, Path("relative-runner"), "-CheckOnly"), capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
