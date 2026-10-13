@echo off
rem Liga a GPU do Wan Studio no Lightning AI e abre o site. Detalhes em lightning\ligar.py.
chcp 65001 >nul
title Wan Studio - Ligar GPU (Lightning)
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python lightning\ligar.py %*
) else (
  py -3 lightning\ligar.py %*
)
echo.
pause
