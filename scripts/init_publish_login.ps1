param(
    [Alias("AccountKey")]
    [string]$Account = "default"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonCandidates = @(
    $env:TOUTIAO_PYTHON,
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    "C:\Users\jiafeng.zeng\AppData\Local\Python\bin\python.exe",
    "py",
    "python"
) | Where-Object { $_ -and $_.Trim() }

function Resolve-Python {
    param(
        [string[]]$Candidates
    )

    foreach ($candidate in $Candidates) {
        if ($candidate -eq "py") {
            try {
                $pyCommand = Get-Command py.exe -ErrorAction Stop
                $resolved = (& $pyCommand.Source -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1)
                $resolved = if ($resolved) { ([string]$resolved).Trim() } else { "" }
                if ($resolved -and (Test-Path -LiteralPath $resolved)) {
                    return $resolved
                }
            }
            catch {
                continue
            }
        }

        if ($candidate -eq "python") {
            try {
                $null = & python --version
                return "python"
            }
            catch {
                continue
            }
        }

        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    throw "No usable Python interpreter was found. Set TOUTIAO_PYTHON if needed."
}

$PythonExe = Resolve-Python -Candidates $PythonCandidates

$BootstrapScript = @'
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(sys.argv[1]).resolve()
ACCOUNT_RAW = sys.argv[2] if len(sys.argv) > 2 else "default"

SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toutiao_content_factory.config import DEFAULT_ACCOUNT_KEY, load_settings, normalize_account_key, sanitize_account_slug

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
    ) from exc


def _resolve_account_key(raw_value: str) -> str:
    normalized = normalize_account_key(raw_value)
    if normalized == DEFAULT_ACCOUNT_KEY:
        return normalized

    slug_target = sanitize_account_slug(normalized)
    base_settings = load_settings(PROJECT_ROOT, account_key=DEFAULT_ACCOUNT_KEY)
    candidates = []
    seen = set()
    for key in base_settings.available_account_keys:
        if key in seen:
            continue
        seen.add(key)
        try:
            settings = load_settings(PROJECT_ROOT, account_key=key)
        except Exception:
            continue
        candidates.append((settings.account_key, settings.account_slug))

    for key, slug in candidates:
        if normalized == key or slug_target == sanitize_account_slug(slug):
            return key
    raise SystemExit(
        f"Unknown account: {raw_value}. Available accounts: "
        + ", ".join(key for key, _slug in candidates)
    )


def _prompt_close(label: str) -> None:
    print("")
    print(f"{label} login window is open.")
    print("Complete login in the browser and confirm the backend page is reachable.")
    print("After login succeeds, return to the terminal and press Enter to close the browser.")
    try:
        input()
    except EOFError:
        pass


def main() -> int:
    account_key = _resolve_account_key(ACCOUNT_RAW)
    settings = load_settings(PROJECT_ROOT, account_key=account_key)
    toutiao_profile = Path(settings.publishing.user_data_dir)
    if not toutiao_profile.is_absolute():
        toutiao_profile = (settings.project_root / toutiao_profile).resolve()
    wechat_profile = (settings.account_runtime_dir / "wechat_profile").resolve()

    toutiao_profile.mkdir(parents=True, exist_ok=True)
    wechat_profile.mkdir(parents=True, exist_ok=True)

    print(f"Using account: {settings.account_key} ({settings.account_slug})")
    print(f"Toutiao profile: {toutiao_profile}")
    print(f"WeChat profile: {wechat_profile}")

    with sync_playwright() as playwright:
        toutiao_context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(toutiao_profile),
            headless=False,
            channel=settings.publishing.channel or None,
            executable_path=settings.publishing.executable_path or None,
        )
        wechat_context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(wechat_profile),
            headless=False,
            channel=settings.publishing.channel or None,
            executable_path=settings.publishing.executable_path or None,
        )
        try:
            toutiao_page = toutiao_context.pages[0] if toutiao_context.pages else toutiao_context.new_page()
            wechat_page = wechat_context.pages[0] if wechat_context.pages else wechat_context.new_page()

            toutiao_page.goto("https://mp.toutiao.com/", wait_until="domcontentloaded", timeout=60000)
            wechat_page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded", timeout=60000)

            print("")
            print("Opened Toutiao and WeChat backend login pages.")
            print("For Toutiao, open the article publish page after login.")
            print("For WeChat, open the dashboard home page or drafts page after login.")
            _prompt_close("Browser")
        finally:
            toutiao_context.close()
            wechat_context.close()

    summary = {
        "account_key": settings.account_key,
        "account_slug": settings.account_slug,
        "toutiao_profile_dir": str(toutiao_profile),
        "wechat_profile_dir": str(wechat_profile),
    }
    print("")
    print("Login session initialization finished.")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'@

Write-Host "Using Python: $PythonExe"
Write-Host "Launching multi-platform login session initialization..."

$TempScript = Join-Path $ProjectRoot ".runtime\init_multi_login_tmp.py"
$TempDir = Split-Path -Parent $TempScript
if (-not (Test-Path -LiteralPath $TempDir)) {
    New-Item -ItemType Directory -Path $TempDir | Out-Null
}

try {
    Set-Content -LiteralPath $TempScript -Value $BootstrapScript -Encoding UTF8
    & $PythonExe $TempScript $ProjectRoot $Account
    exit $LASTEXITCODE
}
finally {
    if (Test-Path -LiteralPath $TempScript) {
        Remove-Item -LiteralPath $TempScript -Force
    }
}
