CreateObject("WScript.Shell").Run _
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""F:\Automated-workflow-main\scripts\scheduled_task_runner.ps1"" --AccountKey default", _
    0, _
    True
