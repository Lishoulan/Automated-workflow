import json
import os
import subprocess
import sys

import pytest

from scripts import run_publication_stress as runner


@pytest.mark.parametrize("case", ["missing", "stale", "malformed", "skipped", "contradictory", "timeout", "passed"])
def test_runner_requires_current_complete_non_skipped_stress_evidence(tmp_path, monkeypatch, case):
    output = tmp_path / "evidence"
    output.mkdir()
    xml = output / "tests.xml"
    valid = '<testsuites><testsuite><testcase name="test_load" classname="tests.test_publication_stress" time="1"/></testsuite></testsuites>'
    if case in {"stale", "timeout"}:
        xml.write_text(valid)
        os.utime(xml, (1, 1))
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-sensitive-value")
    monkeypatch.setenv("GH_TOKEN", "dummy-sensitive-value")
    monkeypatch.setattr(sys, "argv", ["run_publication_stress.py", "--output", str(output)])
    def execute(command, **kwargs):
        assert "OPENAI_API_KEY" not in kwargs["env"] and "GH_TOKEN" not in kwargs["env"]
        assert kwargs["env"]["RUN_PUBLICATION_STRESS"] == "1"
        if case == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if case not in {"missing", "stale"}:
            data = valid
            if case == "malformed": data = "<broken"
            if case == "skipped": data = valid.replace('/>', '><skipped/></testcase>')
            if case == "contradictory": data = valid.replace('/>', '><failure/></testcase>')
            xml.write_text(data)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(runner, "run_process_tree", execute)
    code = runner.main()
    report = json.loads((output / "stress-report.json").read_text())
    assert (code == 0) is (case == "passed")
    assert report["status"] == ("passed" if case == "passed" else "timeout" if case == "timeout" else "incomplete_report")
    if case in {"stale", "timeout", "missing", "malformed"}:
        assert report["cases"] == []
