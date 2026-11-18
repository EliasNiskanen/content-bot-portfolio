@echo off
rem YouTube-kirjautuminen (kerran): toimii mistä tahansa kansiosta
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m bot.auth_youtube
