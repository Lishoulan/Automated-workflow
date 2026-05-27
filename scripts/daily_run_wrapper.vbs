Dim shell
Dim command
Dim i
Dim arg
Dim exitCode

Set shell = CreateObject("WScript.Shell")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass"

For i = 0 To WScript.Arguments.Count - 1
    arg = WScript.Arguments(i)
    arg = Replace(arg, """", """""")
    command = command & " """ & arg & """"
Next

exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
