@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
    py -3 travel_ledger.py
    if errorlevel 1 pause
    exit /b
)
python -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
    python travel_ledger.py
    if errorlevel 1 pause
    exit /b
)
echo Python with Tkinter was not found.
echo Please install Python 3.10 or newer with Tcl/Tk and add it to PATH.
pause
exit /b 1
