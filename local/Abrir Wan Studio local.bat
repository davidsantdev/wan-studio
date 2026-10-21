@echo off
rem Abre o Wan Studio aqui no PC, so a aba "Trocar rosto -> Video" (FaceFusion na sua GPU).
rem O site abre sozinho no navegador em http://127.0.0.1:8080 . Feche esta janela para desligar.
setlocal
set "AQUI=%~dp0"
set "PY=%AQUI%..\..\facefusion-local\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Primeira vez: instalando a troca de rosto em video...
  python "%AQUI%instalar.py" || ( echo. & echo Nao consegui instalar. Confira o Python e o git. & pause & exit /b 1 )
)
start "" http://127.0.0.1:8080
"%PY%" "%AQUI%servidor.py"
echo.
pause
