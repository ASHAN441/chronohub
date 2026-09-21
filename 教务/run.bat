@echo off
setlocal
set "PYW=C:\Users\ashan\AppData\Local\Programs\Python\Python313\pythonw.exe"
set "PY=C:\Users\ashan\AppData\Local\Programs\Python\Python313\python.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0run.py"
) else if exist "%PY%" (
    start "" "%PY%" "%~dp0run.py"
) else (
    start "" python "%~dp0run.py"
)
endlocal
