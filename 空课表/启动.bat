@echo off
setlocal
cd /d "%~dp0"

set "PYEXE="
for /d %%D in (%LOCALAPPDATA%\Programs\Python\Python*) do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE (
    where py >nul 2>nul
    if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo [Error] Python 3 not found. Please install Python from python.org.
    pause
    exit /b 1
)

if not exist ".deps_installed" (
    echo First run: installing dependencies...
    %PYEXE% -m pip install -r requirements.txt --disable-pip-version-check
    if errorlevel 1 (
        echo [Error] Failed to install dependencies. Please check network and retry.
        pause
        exit /b 1
    )
    type nul > ".deps_installed"
)

echo Starting...
%PYEXE% -m app.main
if errorlevel 1 (
    echo [Error] App failed to start.
    pause
)

endlocal
