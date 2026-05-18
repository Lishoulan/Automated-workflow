param(
    [string]$TaskPrefix = "ToutiaoContentFactory",
    [string]$AccountKey = "default",
    [int]$Count = 4
)

$ErrorActionPreference = "Stop"
$AccountSlug = (($AccountKey -replace '[^a-zA-Z0-9]+', '-') -replace '(^-+|-+$)', '').ToLower()
if ([string]::IsNullOrWhiteSpace($AccountSlug)) {
    $AccountSlug = "default"
}
$taskNames = New-Object System.Collections.Generic.List[string]
for ($index = 1; $index -le $Count; $index++) {
    $taskNames.Add("$TaskPrefix-$AccountSlug-$index")
    if ($AccountSlug -eq "default") {
        $taskNames.Add("$TaskPrefix-$index")
    }
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
