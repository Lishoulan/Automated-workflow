$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$toRemove = @(
    'electron.app.SodaMusic',
    'Docker Desktop',
    'GameViewer',
    'QuarkUpdaterTaskUser1.0.0.21',
    'com.minimax.agent.cn',
    'WeChatPublishDaemon',
    'JianyingPro',
    'MicrosoftEdgeAutoLaunch_98769996E24836F99EC8617644423B4C'
)

foreach ($name in $toRemove) {
    $val = Get-ItemProperty -Path $runKey -Name $name -ErrorAction SilentlyContinue
    if ($val) {
        Remove-ItemProperty -Path $runKey -Name $name -ErrorAction Stop
        Write-Host "[REMOVED] $name" -ForegroundColor Green
    } else {
        Write-Host "[SKIP] $name not found" -ForegroundColor DarkGray
    }
}
