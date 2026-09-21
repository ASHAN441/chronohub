@echo off
rem ==================================================================
rem  KongKeBiaoPRO  -  double click this file to start the application.
rem
rem  First run : finds a Python that has every dependency, installs
rem              the dependencies if needed, then remembers the
rem              interpreter path in .runtime
rem  Later runs: starts straight away, no console window
rem
rem  Diagnose  : run this file with the argument  --check
rem              resolves the interpreter without starting the app
rem  Reset     : delete .runtime, then run again
rem
rem  Note: keep this file pure ASCII. cmd.exe reads .bat with the OEM
rem  code page, so UTF-8 comments would show up as mojibake. That is also
rem  why the product name is written here as "KongKeBiaoPRO" instead of the
rem  Chinese original; the app window shows the real name.
rem ==================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title KongKeBiaoPRO

set "STAMP=.runtime"
set "CHECK=%~dp0tools\check_deps.py"
set "PYEXE="

rem ---------- fast path: a previous setup succeeded ----------
if exist "%STAMP%" set /p PYEXE=<"%STAMP%"
if defined PYEXE (
    if exist "%PYEXE%" goto :resolved
    if /i "%PYEXE%"=="python" goto :resolved
    if /i "%PYEXE%"=="py -3" goto :resolved
    set "PYEXE="
)

rem ---------- setup ----------
echo.
echo   KongKeBiaoPRO - first time setup
echo   ----------------------------------------------------------
echo.

rem 1. prefer a python.org install under LOCALAPPDATA that already
rem    has all the dependencies
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%~fD\python.exe" (
        "%%~fD\python.exe" "%CHECK%" >nul 2>nul
        if not errorlevel 1 set "PYEXE=%%~fD\python.exe"
    )
)

rem 2. the py launcher or a PATH python, but only when deps are ready
if not defined PYEXE (
    py -3 "%CHECK%" >nul 2>nul
    if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
    python "%CHECK%" >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)

if defined PYEXE (
    echo   Dependencies are already installed, nothing to do.
    goto :remember
)

rem 3. no interpreter has the deps yet - pick one and install them
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%~fD\python.exe" set "PYEXE=%%~fD\python.exe"
)
if not defined PYEXE (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
    echo   [X] Python 3 was not found on this computer.
    echo.
    echo       Install it from https://www.python.org/downloads/
    echo       and tick the "Add python.exe to PATH" box while installing.
    echo.
    pause
    exit /b 1
)

echo   Interpreter : %PYEXE%
echo   Installing dependencies, this happens only once...
echo.
if /i "%PYEXE%"=="py -3" (
    py -3 -m pip install -r requirements.txt --disable-pip-version-check
) else if /i "%PYEXE%"=="python" (
    python -m pip install -r requirements.txt --disable-pip-version-check
) else (
    "%PYEXE%" -m pip install -r requirements.txt --disable-pip-version-check
)
if errorlevel 1 (
    echo.
    echo   [X] Could not install the dependencies.
    echo       Check the network connection, then run this file again.
    echo.
    pause
    exit /b 1
)

if not exist "%CHECK%" (
    echo   [X] tools\check_deps.py is missing, the download looks incomplete.
    echo.
    pause
    exit /b 1
)

:remember
rem remember the interpreter so later launches skip all of the above
> "%STAMP%" echo %PYEXE%

:resolved
set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"

if /i "%~1"=="--check" (
    echo.
    echo   ---- KongKeBiaoPRO launcher check ----
    echo   interpreter : %PYEXE%
    echo   pythonw     : %PYWEXE%
    if exist "%PYWEXE%" (
        echo   pythonw ok  : yes, the app starts without a console
    ) else (
        echo   pythonw ok  : no, python.exe will be used with a console
    )
    echo   stamp file  : %STAMP%
    endlocal
    exit /b 0
)

rem ---------- launch ----------
rem pythonw keeps the console window out of the way. If it is not
rem available, for example with a non python.org build, fall back to
rem plain python, which shows a console window while the app runs.
if exist "%PYWEXE%" (
    start "KongKeBiaoPRO" /d "%~dp0" "%PYWEXE%" "%~dp0run.py"
) else if /i "%PYEXE%"=="py -3" (
    start "KongKeBiaoPRO" /d "%~dp0" py -3 "%~dp0run.py"
) else if /i "%PYEXE%"=="python" (
    start "KongKeBiaoPRO" /d "%~dp0" python "%~dp0run.py"
) else (
    start "KongKeBiaoPRO" /d "%~dp0" "%PYEXE%" "%~dp0run.py"
)

endlocal
