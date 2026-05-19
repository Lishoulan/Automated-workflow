param(
    [string]$AccountKey = "default"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"
$AccountSlug = (($AccountKey -replace '[^a-zA-Z0-9]+', '-') -replace '(^-+|-+$)', '').ToLower()
if ([string]::IsNullOrWhiteSpace($AccountSlug)) {
    $AccountSlug = "default"
}
$RelativeProfileDir = if ($AccountSlug -eq "default") {
    ".runtime/toutiao_profile"
}
else {
    ".runtime/accounts/$AccountSlug/toutiao_profile"
}

if ($AccountSlug -eq "default" -and (Test-Path -LiteralPath $EnvFile)) {
    foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }

        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }

        if ($parts[0].Trim() -eq "TOUTIAO_BROWSER_PROFILE_DIR") {
            $candidate = $parts[1].Trim()
            if ($candidate) {
                $RelativeProfileDir = $candidate
            }
            break
        }
    }
}

$ProfileDir = if ([System.IO.Path]::IsPathRooted($RelativeProfileDir)) {
    [System.IO.Path]::GetFullPath($RelativeProfileDir)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $RelativeProfileDir))
}

$ProjectRootResolved = [System.IO.Path]::GetFullPath($ProjectRoot)
if (-not $ProfileDir.StartsWith($ProjectRootResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to delete profile dir outside project root: $ProfileDir"
}

if (-not (Test-Path -LiteralPath $ProfileDir)) {
    Write-Host "Profile dir does not exist: $ProfileDir"
    exit 0
}

Write-Host "Removing publish login profile: $ProfileDir"
Remove-Item -LiteralPath $ProfileDir -Recurse -Force
Write-Host "Publish login profile reset completed."
