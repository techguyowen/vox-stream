' VoxStream Silent Desktop & Taskbar Launcher
' Launches the VoxStream Control Center without any black CMD console window.

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strPath = fso.GetParentFolderName(WScript.ScriptFullName)

' Check if virtual environment pythonw.exe exists
pythonw = strPath & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    ' Fall back to system pythonw or check Scripts\python.exe
    If fso.FileExists(strPath & "\.venv\Scripts\python.exe") Then
        pythonw = strPath & "\.venv\Scripts\python.exe"
    Else
        pythonw = "pythonw.exe"
    End If
End If

WshShell.CurrentDirectory = strPath
' Run pythonw silently (0 = hide command window)
WshShell.Run """" & pythonw & """ -m obs_captioner.launcher", 0, False
