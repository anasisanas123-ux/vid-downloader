@echo off
rem One-time setup: Python venv + portable ffmpeg in .\bin (nothing installed system-wide)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv || python -m venv .venv || (echo Python 3.10+ is required & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if not exist "bin\ffmpeg.exe" (
  echo Downloading ffmpeg...
  if not exist bin mkdir bin
  curl.exe -L -o "%TEMP%\ffmpeg.zip" https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip
  if exist "%TEMP%\ffx" rmdir /s /q "%TEMP%\ffx"
  mkdir "%TEMP%\ffx"
  tar.exe -xf "%TEMP%\ffmpeg.zip" -C "%TEMP%\ffx"
  for /d %%D in ("%TEMP%\ffx\*") do copy /y "%%D\bin\ffmpeg.exe" bin\ >nul & copy /y "%%D\bin\ffprobe.exe" bin\ >nul
  rmdir /s /q "%TEMP%\ffx" & del "%TEMP%\ffmpeg.zip"
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1"
echo Setup complete.
