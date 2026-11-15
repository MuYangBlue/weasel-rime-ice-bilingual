@echo off
setlocal
set "ROOT=%~dp0.."
set "APP=%~dp0dist\小狼毫用户中心\小狼毫用户中心.exe"
if exist "%APP%" (
    start "" "%APP%"
    goto :eof
)
where pythonw.exe >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0main.py"
) else (
    start "" python "%~dp0main.py"
)
endlocal
