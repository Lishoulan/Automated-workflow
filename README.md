# AI Toutiao Content Factory

An automated content workflow for Chinese tech-industry media accounts.

It can:
- collect trending topics from configured sources
- analyze data and industry signals
- generate titles, article body, tags, summary, and image prompts
- generate cover and infographic assets
- review and approve runs
- auto-publish to Toutiao Creator Center with Playwright
- manage scheduled daily runs and a local web console

## Stack

- Python
- FastAPI + Uvicorn
- Playwright
- OpenAI-compatible text/image APIs
- Pillow / Matplotlib / Jinja2

## Project Structure

```text
.
|- src/                         # application source
|- scripts/                     # bootstrap, scheduling, login, web startup
|- prompts/                     # prompt templates
|- templates/                   # HTML templates
|- static/                      # web assets
|- input/                       # optional manual input files
|- main.py                      # CLI entry
|- webapp.py                    # web console entry
|- config.yaml                  # app configuration
|- requirements.txt
|- .env.example
`- README.md
```

Runtime directories such as `output/`, `logs/`, `cache/`, `.runtime/`, and `.venv/` are intentionally excluded from Git.

## Quick Start On A New Computer

### 1. Requirements

- Windows 10/11
- Python 3.11+ available as `python`
- Microsoft Edge installed

### 2. Clone the Repository

```powershell
git clone <your-repo-url>
cd <your-repo-folder>
```

### 3. Bootstrap The Environment

This creates `.venv`, installs dependencies, installs Playwright Chromium, and creates `.env` from `.env.example` if needed.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

### 4. Fill In `.env`

Edit `.env` and set at minimum:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

If you use an OpenAI-compatible relay for images:

- `IMAGE_BACKEND=image2`
- `IMAGE_API_KEY`
- `IMAGE_BASE_URL`
- `IMAGE_MODEL`

If you want auto-publish:

- `TARGET_PLATFORM=all`
- `TOUTIAO_PUBLISH_ENABLED=1`
- `TOUTIAO_AUTO_PUBLISH=1`
- `WECHAT_ENABLED=1`
- `WECHAT_AUTO_PUBLISH=1`
- `WECHAT_APP_ID`
- `WECHAT_APP_SECRET`

To let the daily workflow try multiple candidate topics before giving up:

- `TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS=3`

### 5. Initialize Toutiao + WeChat Login

This opens the persistent browser sessions used by the scheduled workflow and lets you log in to Toutiao Creator Center and WeChat Official Accounts once.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1
```

### 6. Start The Web Console

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_webapp.ps1
```

Default URL:

```text
http://127.0.0.1:8000
```

If port `8000` is occupied:

```powershell
python .\webapp.py --port 8001
```

## Common Commands

### Generate One Manual Run

```powershell
python .\main.py
```

Run for a specific account:

```powershell
python .\main.py --account robot_notes
python .\main.py --account energy_notes
```

### Collect Topics Only

```powershell
python .\main.py --topics-only
```

Or for a specific account:

```powershell
python .\main.py --account robot_notes --topics-only
```

### Run The Full Daily Pipeline

```powershell
python .\main.py --daily-run --refresh-cache
```

Or for a specific account:

```powershell
python .\main.py --account robot_notes --daily-run --refresh-cache
```

`--daily-run` now acts as an autopilot flow:

- it collects fresh topics
- it tries up to `TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS` candidates
- it auto-approves high-quality runs
- it auto-publishes Toutiao when `TOUTIAO_AUTO_PUBLISH=1`
- it also creates WeChat drafts, and submits them when `WECHAT_AUTO_PUBLISH=1`
- set `TARGET_PLATFORM=all` if you want one scheduled workflow to handle every enabled platform together

If Toutiao publish fails with `code=2010` / no publish permission, the browser session reached the platform but the logged-in account itself cannot publish from Creator Center. Re-run login initialization with the correct account and verify manual publishing works first.

### Publish The Latest Approved Run

```powershell
python .\main.py --publish-latest-approved
```

WeChat draft / publish:

```powershell
python .\main.py --prepare-wechat-launch-day 2
python .\main.py --prepare-wechat-launch-week
python .\main.py --generate-images-run --run-dir "D:\path\to\output\runs\your_run_dir"
python .\main.py --wechat-draft-latest-approved
python .\main.py --wechat-publish-latest-approved
python .\main.py --init-wechat-login
```

`--wechat-draft-latest-approved` uses the official WeChat API to create a draft. `--wechat-publish-latest-approved` now creates the draft and then uses the logged-in browser session to submit it from the 草稿箱.
`--prepare-wechat-launch-day` and `--prepare-wechat-launch-week` build WeChat launch-week article packages and matching cover / infographic prompt files using the same visual strategy as the Toutiao workflow.
`--generate-images-run` regenerates the real image assets for any existing run package.

Optional `md2wechat` layout backend for WeChat:

```powershell
WECHAT_LAYOUT_BACKEND=md2wechat
WECHAT_MD2WECHAT_ENABLED=1
MD2WECHAT_THEME=default
MD2WECHAT_CLI_PATH=
```

If you have the local `md2wechat` CLI installed, the WeChat flow can use it without any API key. `MD2WECHAT_CLI_PATH` is optional and only needed when the command is not already on `PATH`.

If `MD2WECHAT_API_KEY` is present, the flow prefers the hosted API. If no key is provided, it tries the local CLI. If both are unavailable and `MD2WECHAT_FALLBACK_TO_BUILTIN=1`, it automatically falls back to the built-in HTML renderer.

Or for a specific account:

```powershell
python .\main.py --account robot_notes --publish-latest-approved
python .\main.py --account energy_notes --publish-latest-approved
```

Growth ops / benchmarking:

```powershell
python .\main.py --build-benchmark-report
python .\main.py --backfill-performance-ledger
python .\main.py --build-performance-report
python .\main.py --record-performance --run-dir "D:\path\to\output\runs\your_run_dir" --performance-platform wechat --snapshot-date 2026-05-20 --impression-count 10000 --read-count 860 --recommendation-read-count 520 --like-count 22 --wow-count 11 --share-count 6 --comment-count 8 --follower-gain 17
```

- Benchmark source file: `branding/benchmark_accounts.yaml`
- Self benchmark source file: `branding/self_benchmark_articles.yaml`
- Generated benchmark report: `output/growth_ops/benchmark_report.md`
- Generated self benchmark report: `output/growth_ops/self_benchmark_report.md`
- Performance ledger: `output/growth_ops/performance/performance_ledger.json`
- Performance review report: `output/growth_ops/performance/performance_report.md`
- Each content run now exports `output/benchmark_strategy.json`, and the same benchmark strategy is injected into trend analysis, data extraction, article writing, and review scoring so the workflow follows the selected benchmark-account playbook instead of using benchmarks only for titles.
- The benchmark layer now has two sources: external benchmark accounts and your own validated high-performing articles. Fill `branding/self_benchmark_articles.yaml`, then keep appending post-publish metrics with `--record-performance` so the workflow gradually learns from your own winners.

### Publish A Specific Run

```powershell
python .\main.py --publish-run --run-dir "D:\path\to\output\runs\your_run_dir"
```

## Schedule Automatic Daily Runs

Register 4 tasks at `08:00`, `12:00`, `18:30`, and `22:00`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -EnableAutoPublish
```

Register all configured accounts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_all_accounts.ps1 -EnableAutoPublish
```

Register the separate WeChat-only task at `07:00`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_wechat_daily_task.ps1 -AccountKey default -EnableAutoPublish
```

Unregister tasks manually in Windows Task Scheduler, or extend this repo with your own cleanup script.

## Multi-Account Rules

The project already uses one shared workflow for the default account and all sub-accounts. There is no separate publish implementation per account.

- `default` uses root runtime directories such as `input/`, `output/`, `logs/`, `cache/`, and `.runtime/toutiao_profile/`
- sub-accounts use isolated directories under `input/accounts/<slug>/`, `output/accounts/<slug>/`, `logs/accounts/<slug>/`, `cache/accounts/<slug>/`, and `.runtime/accounts/<account_key>/toutiao_profile/`
- the same `main.py`, review flow, scheduler scripts, and Playwright publish service are reused for every account
- each account must still complete its own Toutiao login initialization once, because browser sessions are isolated per account profile
- WeChat also uses an isolated browser profile per account; run `python .\main.py --account <key> --init-wechat-login` once before browser submission

Currently configured first-wave accounts:

- `default` -> `??????`
- `robot_notes` -> `???????`
- `energy_notes` -> `??????`

Initialize login for each account:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1 -AccountKey default
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1 -AccountKey robot_notes
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1 -AccountKey energy_notes
```

## Migration Notes

To move this project to another computer, copy only:

- source files in this repository
- your `.env`

Do not copy these blindly unless you explicitly want the old machine state:

- `.venv/`
- `output/`
- `logs/`
- `cache/`

You may copy this if you want to preserve Toutiao login state exactly:

- `.runtime/toutiao_profile/`

But the safer option is to re-run login initialization on the new machine.

## Publishing Notes

Current publish flow supports:

- title + article body
- first inline image as the lead cover image
- second inline article image inserted later between paragraphs
- first-release declaration
- work declaration adjustments

Shared publish strategy for all accounts:

- `cover.png` is inserted at the beginning of the article body and is used by Toutiao as the lead cover image
- `infographic_1.png` is inserted later at a paragraph boundary, not in the middle of a sentence
- the publish flow does not depend on the unstable dedicated cover upload panel unless it must fall back
- duplicate inline image assets are deduplicated before upload
- title length is clipped before publish to fit Toutiao limits

This means the default account and every configured sub-account now share the same stable image publishing path.

Because Toutiao publish is implemented through browser automation rather than an official publish API, selector changes on Toutiao may require maintenance.

WeChat publishing uses the same approved run package and converts it into公众号草稿/发布 payloads.

## Security

Do not commit:

- `.env`
- API keys
- browser profile directories
- publish debug outputs

## Upload To GitHub

If your current machine has `git` installed:

```powershell
git init
git add .
git commit -m "Initial commit"
git remote add origin <your-github-repo-url>
git branch -M main
git push -u origin main
```

If this machine does not have `git`, install Git for Windows first, then run the commands above.


## Douyin First Version

A first Douyin workflow is now wired into the project for `??` positioning with browser-session based publishing prep.

Current commands:

```powershell
python .\main.py --prepare-douyin-run --run-dir "D:\path	o\output
uns\your_run_dir"
python .\main.py --init-douyin-login
```

What it does today:

- builds a Douyin-ready package from an approved run under `publish/douyin/`
- exports `douyin_payload.json`, clipped title, caption, hashtags, and publish notes
- reuses generated images so the same visual assets can feed Douyin??
- stores a separate persistent browser profile for each account

Current limitation:

- the browser publish flow is now wired, but live creator selectors may still need one calibration pass against your own account session
- `DOUYIN_DAILY_POST_TARGET=10` is supported as a planning target, but for new accounts a lower real publish frequency is still safer for distribution and risk control

Recommended config baseline:

- `DOUYIN_NICHE=??`
- `DOUYIN_PUBLISH_MODE=browser`
- `DOUYIN_DAILY_POST_TARGET=10`

After you log in once with `--init-douyin-login`, we can do the next step: lock selectors from your actual creator page and finish the auto-publish loop.

