"""Run a bounded, isolated publication stress suite and save reviewable evidence."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from toutiao_content_factory.services.wechat_execution import run_process_tree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/publication-stress")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    # Legacy scheduler modules open a log during test collection. A clean
    # hosted runner has no output directory; production cloud entrypoints
    # normally create it before launching that scheduler.
    (ROOT / "output").mkdir(exist_ok=True)
    env = dict(os.environ)
    for key in list(env):
        if any(word in key.upper() for word in ("TOKEN", "SECRET", "API_KEY", "SESSION_SEED", "SESSION_KEY")):
            env.pop(key)
    # Keep real run provenance in the parent report, while simulated cloud
    # scenarios explicitly install their own IDs in the test process.
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_ACTIONS", "GITHUB_SHA"):
        env.pop(key, None)
    env.update(RUN_PUBLICATION_STRESS="1", PUBLICATION_STRESS_SCALE=str(args.scale),
               PYTHONUTF8="1", WECHAT_FREE_COVER_ONLY="0",
               PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT)]))
    suite = ["test_publication_stress.py", "test_wechat_audit_integrity.py", "test_wechat_draft_delivery.py",
             "test_toutiao_read_conversion.py", "test_reader_value_rollout.py", "test_reader_value_experiment.py",
             "test_weitoutiao_generator.py", "test_material_recovery.py",
             "test_growth_feedback_service.py", "test_growth_checkpoints.py", "test_growth_wechat_automation.py",
             "test_wechat_publication_loop.py", "test_cloud_material_supply.py", "test_wechat_growth_service.py",
             "test_generation_cost_service.py", "test_revenue_experiment_service.py", "test_publication_stress_runner.py",
             "test_cloud_publication_fault_stress.py", "test_publication_watchdog.py", "test_platform_health_service.py",
             "test_wechat_cloud.py", "test_wechat_cloud_metrics.py", "test_wechat_publish_recovery.py",
             "test_wechat_reviewed_cloud.py", "test_wechat_structured_request_limits.py",
             "test_publish_retry_no_duplicate.py", "test_image_generation_service.py"]
    command = [sys.executable, "-m", "pytest", "-p", "scripts.publication_stress_guard", "-q",
               "-o", "addopts=", "-o", "junit_family=xunit1", "--junitxml=" + str(args.output / "tests.xml")]
    command.extend(str(ROOT / "tests" / name) for name in suite)
    # Browser transport smoke needs an installed browser; isolated selector and
    # response faults do not. Include only those portable boundary checks here.
    command.append(str(ROOT / "tests/test_wechat_browser_upload.py"))
    command.extend(["-k", "not test_browser_transport_uploads_real_multipart_without_external_network"])
    started, started_ns, status, code = time.monotonic(), time.time_ns(), "failed", 1
    try:
        with (args.output / "pytest.log").open("w", encoding="utf-8") as log:
            result = run_process_tree(command, cwd=ROOT, env=env, timeout=180, stdout=log, stderr=subprocess.STDOUT)
        code = result.returncode
        status = "passed" if code == 0 else "failed"
    except subprocess.TimeoutExpired:
        status = "timeout"
    except Exception as exc:
        status = "runner_error"
        (args.output / "runner-error.txt").write_text(type(exc).__name__, encoding="utf-8")
    cases = []
    xml_path = args.output / "tests.xml"
    if xml_path.is_file() and xml_path.stat().st_mtime_ns >= started_ns:
        try:
            xml_cases = list(ET.parse(xml_path).iter("testcase"))
        except ET.ParseError:
            xml_cases = []
            if status == "passed":
                status, code = "incomplete_report", 1
        for case in xml_cases:
            cases.append({"name": case.get("name"), "seconds": float(case.get("time", 0)),
                          "suite": case.get("classname", ""),
                          "failed": case.find("failure") is not None or case.find("error") is not None,
                          "skipped": case.find("skipped") is not None,
                          "load": {item.get("name"): item.get("value") for item in case.findall("properties/property")}})
    stress_cases = [row for row in cases if row["suite"].endswith(("test_publication_stress", "test_cloud_publication_fault_stress"))]
    required_suites = {"test_publication_stress", "test_cloud_publication_fault_stress"}
    observed_suites = {row["suite"].split(".")[-1] for row in stress_cases}
    if status == "passed" and (not required_suites <= observed_suites or any(row["skipped"] or row["failed"] for row in stress_cases)
                               or any(row["failed"] for row in cases)):
        status, code = "incomplete_report", 1
    report = {"status": status, "scale": args.scale,
              "platform": "Windows" if sys.platform == "win32" else "Linux" if sys.platform.startswith("linux") else sys.platform,
              "elapsed_seconds": round(time.monotonic() - started, 3), "timeout_seconds": 180,
              "passed": sum(not row["failed"] and not row["skipped"] for row in cases),
              "failed": sum(row["failed"] for row in cases), "skipped": sum(row["skipped"] for row in cases),
              "external_network_blocked": True, "loopback_allowed_for_event_loop": True,
              "extra_publication": False, "production_quota_consumed": False,
              "model_calls": 0, "workflow_run": os.environ.get("GITHUB_RUN_ID", ""), "cases": cases}
    report["source_commit"] = os.environ.get("GITHUB_SHA", "")
    (args.output / "stress-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
