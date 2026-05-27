param(
    [string]$TaskPrefix = "ToutiaoContentFactory",
    [string]$AccountKey = "default"
)

$ErrorActionPreference = "Stop"
$AccountSlug = (($AccountKey -replace '[^a-zA-Z0-9]+', '-') -replace '(^-+|-+$)', '').ToLower()
if ([string]::IsNullOrWhiteSpace($AccountSlug)) {
    $AccountSlug = "default"
}
$taskNames = New-Object System.Collections.Generic.List[string]
$defaultPublishCount = 4
for ($index = 1; $index -le $defaultPublishCount; $index++) {
    $taskNames.Add("$TaskPrefix-$AccountSlug-$index")
    if ($AccountSlug -eq "default") {
        $taskNames.Add("$TaskPrefix-$index")
    }
}
$taskNames.Add("$TaskPrefix-$AccountSlug-collect-performance")
if ($AccountSlug -eq "default") {
    $taskNames.Add("$TaskPrefix-collect-performance")
}

$taskNames = $taskNames | Select-Object -Unique
foreach ($taskName in $taskNames) {
    try {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
            Write-Host "Scheduled task removed: $taskName"
        }
        else {
            Write-Host "Scheduled task not found: $taskName"
        }
    }
    catch {
        Write-Host "Failed to remove task: $taskName"
        Write-Host $_
    }
}
