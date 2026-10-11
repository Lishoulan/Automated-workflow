param(
    [Parameter(Mandatory = $true)][string]$RunnerRoot,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [string]$TaskName = 'WeChatActionsRunner',
    [ValidateSet('Auto', 'Task', 'Startup')][string]$Mode = 'Auto',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if (-not [System.IO.Path]::IsPathRooted($RunnerRoot) -or -not [System.IO.Path]::IsPathRooted($PythonPath)) {
    throw 'RunnerRoot and PythonPath must be absolute paths.'
}
$RunnerRoot = [System.IO.Path]::GetFullPath($RunnerRoot)
$PythonPath = [System.IO.Path]::GetFullPath($PythonPath)
if (-not (Test-Path -LiteralPath (Join-Path $RunnerRoot 'bin/Runner.Listener.exe')) -or
    -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Installed runner or Python executable missing.'
}
$Source = Join-Path $PSScriptRoot 'wechat_runner_supervisor.ps1'
$Destination = Join-Path $RunnerRoot 'supervision/supervise.ps1'
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
    $Destination + '" -RunnerRoot "' + $RunnerRoot + '" -PythonPath "' + $PythonPath + '"'
$PowerShell = Join-Path $PSHOME 'powershell.exe'
if ($DryRun) {
    [ordered]@{ task = $TaskName; mode = $Mode; user = $User; execute = $PowerShell; arguments = $Arguments;
        logon = 'Interactive'; retry_minutes = 1; repeat_minutes = 2; start_on_logon = $true;
        publication_action = $false } | ConvertTo-Json
    exit 0
}
$Previous = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Previous) {
    if ($Previous.State -eq 'Running') {
        throw 'The supervisor is running. Pause maintenance and stop its task before replacing it.'
    }
    if ($Previous.Description -ne 'Keep the existing WeChat GitHub Actions runner available; never invoke publication directly.') {
        throw 'Refusing to overwrite an unrelated scheduled task.'
    }
}
New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
Copy-Item -LiteralPath $Source -Destination $Destination -Force
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $RunnerRoot
$Logon = New-ScheduledTaskTrigger -AtLogOn -User $User
$Periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Selected = 'Task'
if ($Mode -eq 'Startup') {
    $Selected = 'Startup'
} else {
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($Logon, $Periodic) -Settings $Settings `
            -Principal $Principal -Description 'Keep the existing WeChat GitHub Actions runner available; never invoke publication directly.' -Force | Out-Null
    } catch {
        if ($Mode -eq 'Task' -or ($_.FullyQualifiedErrorId -notmatch '0x80070005' -and
            $_.Exception -isnot [System.UnauthorizedAccessException])) { throw }
        $Selected = 'Startup'
    }
}
if ($Selected -eq 'Task') {
    Start-ScheduledTask -TaskName $TaskName
} else {
    $Startup = [Environment]::GetFolderPath('Startup')
    if (-not $Startup -or -not [System.IO.Path]::IsPathRooted($Startup)) {
        throw 'Current-user Startup folder unavailable.'
    }
    New-Item -ItemType Directory -Path $Startup -Force | Out-Null
    $ShortcutPath = Join-Path $Startup ($TaskName + '.lnk')
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    if ((Test-Path -LiteralPath $ShortcutPath) -and ($Shortcut.TargetPath -ne $PowerShell -or $Shortcut.Arguments -ne $Arguments)) {
        throw 'Refusing to overwrite an unrelated Startup entry.'
    }
    $Shortcut.TargetPath = $PowerShell
    $Shortcut.Arguments = $Arguments
    $Shortcut.WorkingDirectory = $RunnerRoot
    $Shortcut.WindowStyle = 7
    $Shortcut.Description = 'Keep the existing WeChat GitHub Actions runner available; never invoke publication directly.'
    $Shortcut.Save()
    Start-Process -FilePath $PowerShell -ArgumentList $Arguments -WorkingDirectory $RunnerRoot -WindowStyle Hidden | Out-Null
}
[ordered]@{ task = $TaskName; mode = $Selected; registered = $true; start_requested = $true; user = $User } | ConvertTo-Json
