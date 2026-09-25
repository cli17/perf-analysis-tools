@echo off
setlocal

set "PROXY=http://proxy-dmz.intel.com:912"
set "NO_PROXY=localhost,.local,intel.com,.intel.com,*.intel.com"
set "REPO_DIR=%~dp0"

set "http_proxy=%PROXY%"
set "https_proxy=%PROXY%"
set "HTTP_PROXY=%PROXY%"
set "HTTPS_PROXY=%PROXY%"
set "NO_PROXY=%NO_PROXY%"

set "WORKSPACE=%REPO_DIR%"

where code >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Launching VS Code for %WORKSPACE%
    start "VS Code" code "%WORKSPACE%"
    exit /b 0
)

where code.cmd >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Launching VS Code for %WORKSPACE%
    start "VS Code" code.cmd "%WORKSPACE%"
    exit /b 0
)

set "VSCODE_EXE=%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"
if exist "%VSCODE_EXE%" (
    echo Launching VS Code for %WORKSPACE%
    start "VS Code" "%VSCODE_EXE%" "%WORKSPACE%"
    exit /b 0
)

echo VS Code executable not found on PATH.
exit /b 1
