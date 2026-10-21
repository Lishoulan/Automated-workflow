"""A cache miss must not crash the cloud scheduler before login checks."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_scheduler_import_creates_its_log_parent_in_a_fresh_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "fresh-project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    scheduler = scripts / "scheduled_wechat_publish.py"
    shutil.copy2(root / "scripts/scheduled_wechat_publish.py", scheduler)
    assert not (project / "output").exists()

    result = subprocess.run(
        [sys.executable, "-c", "import runpy,sys; runpy.run_path(sys.argv[1])", str(scheduler)],
        cwd=project, env={**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONUTF8": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert (project / "output/wechat_scheduled_publish.log").is_file()
