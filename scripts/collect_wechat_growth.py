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
    host = os.environ.get("WECHAT_EXECUTION_HOST", "cloud")
    settings = None
    if host == "fixed":
        fixed = Path(os.environ["WECHAT_FIXED_DEVICE_DIR"])
        if not fixed.is_absolute():
            raise ValueError("Fixed-device directory must be absolute")
        storage = fixed / "default/automation/browser_state/WeChat_storage_state.json"
        channel = "msedge"
    elif host == "cloud":
        from toutiao_content_factory.config import load_settings
        from wechat_cloud_session import restore_session, save_session

        settings = load_settings(ROOT, account_key="default")
        encrypted = settings.account_runtime_dir / "automation/wechat_cloud_session.enc"
        key = os.environ.get("WECHAT_SESSION_KEY", "")
        storage = restore_session(settings, encrypted, key, os.environ.get("WECHAT_SESSION_SEED", ""))
        channel = None
    else:
        raise ValueError("Unknown WeChat execution host")
    previous = xstate.read_remote_state(ROOT, branch=xstate.WECHAT_STATE_BRANCH, path=growth.STATE_PATH, strict=True)
    observation = growth.collect_home_observation(storage, channel=channel)
    state = growth.merge_observation(previous, observation, account="小椰科技")
    report = growth.build_growth_report(state)
    if settings is not None:
        save_session(settings, encrypted, key)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "wechat_growth_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "wechat_growth_report.md").write_text(growth.render_growth_report(report), encoding="utf-8")
    from toutiao_content_factory.services.generation_cost_service import build_cost_summary
    revenue_report = {"account": report["account"], "observed_at": report["observed_at"],
        "monetization": report["monetization"], "ad_activation": report["ad_activation"],
        "generation_costs": build_cost_summary(settings.output_dir if settings else ROOT / "output"),
        "net_profit": None, "note": "公众号后台金额与头条收入分开记录；未核实扣费、结算及完整成本时不计算净利润。"}
    (args.output / "wechat_revenue_report.json").write_text(json.dumps(revenue_report, ensure_ascii=False, indent=2), encoding="utf-8")
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
