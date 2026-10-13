@echo off
rem Faz a troca de pessoa em todos os videos da pasta producao\videos. Detalhes em free\producao.py.
chcp 65001 >nul
title Wan Studio - Produzir em massa
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python free\producao.py %*
) else (
  py -3 free\producao.py %*
)
echo.
pause
