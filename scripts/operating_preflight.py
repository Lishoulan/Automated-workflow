"""Read-only checkout/production alignment check before Codex edits or release.

Never infer the production commit from an old local checkout. Does not fetch,
checkout, reset, mutate Git refs or write files.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=25, check=False)
    return proc.returncode, proc.stdout.strip()


def inspect_checkout(path: Path, repo: str = "Lishoulan/Automated-workflow") -> dict:
    path = path.resolve()
    result = {"checkout": str(path), "repo": repo, "head": None,
              "production_head": None, "branch": None, "dirty": None,
              "aligned": None, "status": "unverified"}
    try:
        code, head = _run(["git", "-C", str(path), "rev-parse", "HEAD"])
        if code or len(head) != 40:
            result["reason"] = "not_a_git_checkout"
            return result
        result["head"] = head
        code, branch = _run(["git", "-C", str(path), "branch", "--show-current"])
        result["branch"] = branch if code == 0 else None
        code, porcelain = _run(["git", "-C", str(path), "status", "--porcelain"])
        if code == 0:
            result["dirty"] = bool(porcelain)
        code, production = _run(["gh", "api", f"repos/{repo}/commits/main", "--jq", ".sha"])
        if code or len(production) != 40:
            result["reason"] = "remote_main_unverified"
            return result
        result["production_head"] = production
        result["aligned"] = head == production
        if not result["aligned"]:
            result.update(status="blocked", reason="checkout_head_differs_from_production")
        elif result["dirty"] is not False:
            result.update(status="blocked", reason="checkout_has_uncommitted_changes")
        else:
            result["status"] = "aligned_clean"
        return result
    except (OSError, subprocess.TimeoutExpired):
        result["reason"] = "git_or_github_unavailable"
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--repo", default="Lishoulan/Automated-workflow")
    args = parser.parse_args()
    report = inspect_checkout(args.checkout, args.repo)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "aligned_clean" else 2


if __name__ == "__main__":
    raise SystemExit(main())
