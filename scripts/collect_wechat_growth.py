"""Collect homepage observations without invoking the publisher or generation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.services import cross_channel_state as xstate
from toutiao_content_factory.services import wechat_growth_service as growth


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--read-only", action="store_true", help="Save local evidence without changing shared state")
    parser.add_argument("--output", type=Path, default=ROOT / "output/automation")
    args = parser.parse_args()
    if os.environ.get("WECHAT_EXECUTION_HOST") != "fixed":
        raise RuntimeError("This collector requires the existing fixed device")
    fixed = Path(os.environ["WECHAT_FIXED_DEVICE_DIR"])
    if not fixed.is_absolute():
        raise ValueError("Fixed-device directory must be absolute")
    previous = xstate.read_remote_state(ROOT, branch=xstate.WECHAT_STATE_BRANCH, path=growth.STATE_PATH, strict=True)
    observation = growth.collect_home_observation(fixed / "default/automation/browser_state/WeChat_storage_state.json")
    state = growth.merge_observation(previous, observation, account="小椰科技")
    report = growth.build_growth_report(state)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "wechat_growth_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "wechat_growth_report.md").write_text(growth.render_growth_report(report), encoding="utf-8")
    if not args.read_only:
        from wechat_cloud_run import write_state_file
        write_state_file(state, growth.STATE_PATH, "chore(wechat): record read-only content observations [skip ci]")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(growth.render_growth_report(report))
    print(json.dumps({"account": report["account"], "followers": report["followers"],
                      "visible_articles": len(report["articles"]), "shared_state_saved": not args.read_only}, ensure_ascii=False))


if __name__ == "__main__":
    main()
