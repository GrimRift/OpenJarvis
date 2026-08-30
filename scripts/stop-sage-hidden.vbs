Option Explicit

Dim shell, fileSystem, scriptPath, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptPath = fileSystem.BuildPath(fileSystem.GetParentFolderName(WScript.ScriptFullName), "stop-sage.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & scriptPath & """"

exitCode = shell.Run(command, 0, True)

If exitCode = 0 Then
    shell.Popup "Sage has been stopped.", 5, "Stop Sage", 64
ElseIf exitCode = 4 Then
    shell.Popup "Sage is already stopped.", 5, "Stop Sage", 64
ElseIf exitCode = 5 Then
    shell.Popup "A different program is using a Sage port, so nothing was stopped.", 10, "Stop Sage", 48
Else
    shell.Popup "Sage could not be stopped safely.", 10, "Stop Sage", 16
End If
