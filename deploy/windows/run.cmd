@echo off
rem Akim'i Windows'ta konsolda calistirir; hata ile durursa 15 sn sonra yeniden baslatir.
rem Ctrl+C ile temiz kapanir ve yeniden baslatmaz. Ek arguman gecirilebilir: run.cmd -v
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
  echo Once kurulum yapin: deploy\windows\install.cmd
  exit /b 1
)
title Akim

:loop
".venv\Scripts\python.exe" -m akim %* run
if not errorlevel 1 goto :eof
echo [%date% %time%] Akim hata koduyla durdu; 15 sn sonra yeniden baslatiliyor. Durdurmak icin Ctrl+C.
timeout /t 15 /nobreak >nul
goto loop
