@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RetroVault Toolkit - Setup

echo.
echo  ============================================
echo    RetroVault Toolkit - Setup
echo  ============================================
echo.
echo  This sets everything up inside this folder only:
echo    1. finds Python (or helps you install it)
echo    2. makes a private Python environment here (.venv)
echo    3. installs the Python packages the tools need
echo    4. creates the apps folders and checks what is missing
echo.
echo  It is safe to run again at any time.
echo.

rem ---- 1. Find Python 3.10 or newer --------------------------------------
set "PY="
call :trypy py -3
if not defined PY call :trypy python
if not defined PY call :trypy "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY call :trypy "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY call :trypy "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if defined PY goto :havepy

echo  Python 3.10 or newer was not found on this PC.
echo.
where winget >nul 2>nul
if errorlevel 1 goto :manualpy
if "%RV_YES%"=="1" goto :wingetpy
choice /c YN /m "  Install Python 3.13 now (free, from python.org via Windows' winget)"
if errorlevel 2 goto :manualpy
:wingetpy
winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
call :trypy "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if defined PY goto :havepy
echo.
echo  Python was installed, but this window can't see it yet.
echo  Close this window and double-click "Install RetroVault.bat" again.
goto :fail

:manualpy
echo  Please install Python from https://www.python.org/downloads/
echo  IMPORTANT: on the first installer screen tick "Add python.exe to PATH".
echo  Then double-click "Install RetroVault.bat" again.
start "" https://www.python.org/downloads/
goto :fail

:havepy
echo  [1/4] Python found: %PY%

rem ---- 2. Private environment --------------------------------------------
if exist ".venv\Scripts\python.exe" goto :venvok
echo  [2/4] Making the private Python environment (.venv) ...
%PY% -m venv .venv
if errorlevel 1 (
  echo  Could not create .venv. If you use the Microsoft Store Python, install it
  echo  from https://www.python.org/downloads/ instead and run this again.
  goto :fail
)
:venvok
echo  [2/4] Private environment ready.

rem ---- 3. Packages -------------------------------------------------------
echo  [3/4] Installing Python packages (needs internet, takes a minute) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 (
  echo  Package install failed. Check your internet connection and run this again.
  goto :fail
)
echo  [3/4] Packages installed.

rem ---- 4. Folders and checklist -----------------------------------------
echo  [4/4] Checking helper programs ...
".venv\Scripts\python.exe" first_run.py

rem ---- Desktop shortcut ---------------------------------------------------
if "%RV_YES%"=="1" goto :done
if exist "%USERPROFILE%\Desktop\RetroVault Toolkit.lnk" goto :done
choice /c YN /m "  Put a 'RetroVault Toolkit' shortcut on your desktop"
if errorlevel 2 goto :done
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\RetroVault Toolkit.lnk'); $s.TargetPath='%~dp0RetroVault.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo  Shortcut created.

:done
echo.
echo  Setup finished. Start the suite with RetroVault.bat or the desktop shortcut.
echo.
if not "%RV_YES%"=="1" pause
exit /b 0

:fail
echo.
if not "%RV_YES%"=="1" pause
exit /b 1

rem ---- helper: set PY if %* is Python 3.10+ ------------------------------
:trypy
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%*"
exit /b 0
