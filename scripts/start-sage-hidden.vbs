Option Explicit

Dim shell, fileSystem, scriptPath, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptPath = fileSystem.BuildPath(fileSystem.GetParentFolderName(WScript.ScriptFullName), "start-sage.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & scriptPath & """"

exitCode = shell.Run(command, 0, True)

If exitCode = 1 Then
    shell.Popup "Sage did not become ready within two minutes. Check the Sage server and frontend error logs in C:\AI\OpenJarvis-Data\logs.", 15, "Sage", 16
ElseIf exitCode = 2 Then
    shell.Popup "The Sage executable could not be found. Check the OpenJarvis-Lab installation.", 15, "Sage", 16
ElseIf exitCode = 3 Then
    shell.Popup "Node.js/npm could not be found, so the Sage frontend could not start.", 15, "Sage", 16
End If
