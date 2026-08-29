param(
    [string]$Account = "default",
    [string]$Repo = "",
    [string]$ReleaseTag = "browser-profiles-v1",
    [switch]$Headless,
    [switch]$SkipKeepAlive,
    [switch]$SkipCookieExport,
    [switch]$SkipPackProfiles,
    [switch]$SkipUpload
)

$ErrorActionPreference = "Stop"

function Write-Info($Message) {
    Write-Host "[INFO] $Message" -ForegroundColor Cyan
}

function Write-Ok($Message) {
    Write-Host "[ OK ] $Message" -ForegroundColor Green
}

function Write-Warn($Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Resolve-PythonCommand {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    foreach ($name in @("py", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            return $cmd.Name
        }
    }
    throw "Neither 'py' nor 'python' was found in PATH."
}

function Resolve-RepoSlug([string]$ExplicitRepo) {
    if (-not [string]::IsNullOrWhiteSpace($ExplicitRepo)) {
        return $ExplicitRepo.Trim()
    }

    $remoteUrl = (& git remote get-url origin).Trim()
    if (-not $remoteUrl) {
        throw "Unable to determine git remote origin."
    }

    if ($remoteUrl -match 'github\.com[:/](.+?)(?:\.git)?$') {
        return $Matches[1]
    }

    throw "Unsupported git remote format: $remoteUrl"
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [string]$StepName = "command"
    )

    Write-Info("$StepName -> $FilePath $($Arguments -join ' ')")
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$StepName failed with exit code $LASTEXITCODE."
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

$pythonCmd = Resolve-PythonCommand
$repoSlug = Resolve-RepoSlug -ExplicitRepo $Repo
$cookiesOutput = Join-Path $projectRoot ".runtime\automation\exported_cookies.json"
$profileArchive = Join-Path $projectRoot "browser_profiles_seed.tar.gz"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Cloud deployment helper" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Info("Project root: $projectRoot")
Write-Info("Account: $Account")
Write-Info("Repo: $repoSlug")
Write-Info("Release tag: $ReleaseTag")

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI 'gh' is required. Install it first: https://cli.github.com/"
}

Write-Info("Checking GitHub CLI authentication")
& gh auth status
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run 'gh auth login' first."
}

New-Item -ItemType Directory -Force -Path (Split-Path $cookiesOutput -Parent) | Out-Null

if (-not $SkipKeepAlive) {
    $keepAliveArgs = @("scripts/keep_alive.py", "--account", $Account)
    if ($Headless) {
        $keepAliveArgs += "--headless"
    }
    Invoke-CheckedCommand -FilePath $pythonCmd -Arguments $keepAliveArgs -StepName "Refresh local browser sessions"
    Write-Ok("Browser sessions refreshed")
} else {
    Write-Warn("Skipping keep-alive step")
}

if (-not $SkipCookieExport) {
    $exportArgs = @(
        "scripts/export_cookies.py",
        "--account", $Account,
        "--output", $cookiesOutput
    )
    if ($Headless) {
        $exportArgs += "--headless"
    }
    Invoke-CheckedCommand -FilePath $pythonCmd -Arguments $exportArgs -StepName "Export cloud cookies"

    if (-not (Test-Path $cookiesOutput)) {
        throw "Cookie export did not produce $cookiesOutput"
    }

    $cookieFile = Get-Item $cookiesOutput
    if ($cookieFile.Length -le 20) {
        throw "Cookie export output looks too small: $($cookieFile.Length) bytes"
    }

    Write-Ok("Exported cookies -> $cookiesOutput")
} else {
    Write-Warn("Skipping cookie export step")
}

if (-not $SkipPackProfiles) {
    $packArgs = @(
        "scripts/pack_browser_profiles.py",
        "--account", $Account,
        "--output", $profileArchive
    )
    Invoke-CheckedCommand -FilePath $pythonCmd -Arguments $packArgs -StepName "Pack browser profiles"

    if (-not (Test-Path $profileArchive)) {
        throw "Profile pack step did not produce $profileArchive"
    }

    Write-Ok("Packed browser profiles -> $profileArchive")
} else {
    Write-Warn("Skipping profile pack step")
}

if (-not $SkipUpload) {
    Write-Info("Ensuring GitHub release '$ReleaseTag' exists")
    & gh release view $ReleaseTag --repo $repoSlug *> $null
    if ($LASTEXITCODE -ne 0) {
        Invoke-CheckedCommand -FilePath "gh" -Arguments @(
            "release", "create", $ReleaseTag,
            "--repo", $repoSlug,
            "--title", $ReleaseTag,
            "--notes", "Cloud browser profiles seed and exported cookies."
        ) -StepName "Create release"
    }

    $uploadArgs = @("release", "upload", $ReleaseTag)
    if (Test-Path $profileArchive) {
        $uploadArgs += $profileArchive
    }
    if (Test-Path $cookiesOutput) {
        $uploadArgs += $cookiesOutput
    }
    if ($uploadArgs.Count -le 3) {
        throw "No deployable assets found to upload."
    }
    $uploadArgs += @("--clobber", "--repo", $repoSlug)

    Invoke-CheckedCommand -FilePath "gh" -Arguments $uploadArgs -StepName "Upload release assets"
    Write-Ok("Uploaded release assets to $repoSlug@$ReleaseTag")

    Write-Info("Release assets:")
    & gh release view $ReleaseTag --repo $repoSlug --json assets --jq ".assets[].name"
} else {
    Write-Warn("Skipping release upload step")
}

Write-Host ""
Write-Host "Cloud deployment materials are ready." -ForegroundColor Green
Write-Host "Next step: manually trigger 'Daily Run' once in GitHub Actions to verify restore + publish flow." -ForegroundColor Green
