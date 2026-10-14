@echo off
rem Liga a GPU rapida do Wan Studio (Modal) e abre o site. Detalhes em nuvem\ligar.py.
chcp 65001 >nul
title Wan Studio - Ligar GPU rapida
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python nuvem\ligar.py %*
) else (
  py -3 nuvem\ligar.py %*
)
echo.
pause
