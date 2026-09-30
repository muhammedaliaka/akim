@echo off
rem Cift tiklayarak kurulum: sanal ortam + bagimliliklar + config.yaml/.env sablonlari.
rem Laya icin:  install.cmd -WithLaya
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
