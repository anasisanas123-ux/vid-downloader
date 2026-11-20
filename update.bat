@echo off
rem Sites change often - when downloads start failing, run this first.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install -U "yt-dlp[default]"
pause
