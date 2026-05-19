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

- `TOUTIAO_PUBLISH_ENABLED=1`
- `TOUTIAO_AUTO_PUBLISH=1`

### 5. Initialize Toutiao Login

This opens a persistent browser profile and lets you log in to Toutiao Creator Center once.

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

### Publish The Latest Approved Run

```powershell
python .\main.py --publish-latest-approved
```

Or for a specific account:

```powershell
python .\main.py --account robot_notes --publish-latest-approved
python .\main.py --account energy_notes --publish-latest-approved
```

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

Unregister tasks manually in Windows Task Scheduler, or extend this repo with your own cleanup script.

## Multi-Account Rules

The project already uses one shared workflow for the default account and all sub-accounts. There is no separate publish implementation per account.

- `default` uses root runtime directories such as `input/`, `output/`, `logs/`, `cache/`, and `.runtime/toutiao_profile/`
- sub-accounts use isolated directories under `input/accounts/<slug>/`, `output/accounts/<slug>/`, `logs/accounts/<slug>/`, `cache/accounts/<slug>/`, and `.runtime/accounts/<account_key>/toutiao_profile/`
- the same `main.py`, review flow, scheduler scripts, and Playwright publish service are reused for every account
- each account must still complete its own Toutiao login initialization once, because browser sessions are isolated per account profile

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
