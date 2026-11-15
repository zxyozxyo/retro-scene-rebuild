@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First start: running setup ...
  call "Install RetroVault.bat"
  if not exist ".venv\Scripts\pythonw.exe" exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" main.py
