@echo off
rem Liga a GPU do Wan Studio no Kaggle e abre o site. Detalhes em free\ligar.py.
chcp 65001 >nul
title Wan Studio - Ligar GPU
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python free\ligar.py %*
) else (
  py -3 free\ligar.py %*
)
echo.
pause
