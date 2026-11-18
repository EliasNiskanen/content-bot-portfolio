@echo off
rem GTA 6 -videobotti Windowsilla: run.bat run --mock
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m bot.main %*
