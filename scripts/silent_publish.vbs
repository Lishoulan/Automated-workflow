Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File ""F:\Automated-workflow-main\scripts\one_click_publish.ps1""", 0, False
