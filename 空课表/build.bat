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
    echo [Error] Python 3 not found. Please install Python first.
    pause
    exit /b 1
)

echo Installing build dependencies...
%PYEXE% -m pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 (
    echo [Error] Failed to install run dependencies.
    pause
    exit /b 1
)
%PYEXE% -m pip install pyinstaller --disable-pip-version-check -q
if errorlevel 1 (
    echo [Error] Failed to install pyinstaller.
    pause
    exit /b 1
)

echo Building...
%PYEXE% -m PyInstaller --noconfirm --clean --windowed --onefile ^
    --name "多人空课表" ^
    --add-data "web;web" ^
    --hidden-import webview.platforms.edgechromium ^
    run.py
if errorlevel 1 (
    echo [Error] Build failed.
    pause
    exit /b 1
)

echo.
echo Done. The program is at: dist\多人空课表.exe
pause
