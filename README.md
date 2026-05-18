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

### Collect Topics Only

```powershell
python .\main.py --topics-only
```

### Run The Full Daily Pipeline

```powershell
python .\main.py --daily-run --refresh-cache
```

### Publish The Latest Approved Run

```powershell
python .\main.py --publish-latest-approved
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
- single cover upload
- one inline article image upload
- first-release declaration
- work declaration adjustments

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
