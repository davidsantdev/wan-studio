@echo off
rem Troca o rosto de um video pelo rosto de uma foto, aqui no PC (FaceFusion).
rem Arraste a foto do rosto e o video para cima deste arquivo, ou ponha os dois na pasta
rem facefusion-local\entrada (ao lado do projeto) e so de um duplo clique.
setlocal
set "AQUI=%~dp0"
set "PY=%AQUI%..\..\facefusion-local\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Primeira vez: instalando a troca de rosto em video...
  python "%AQUI%instalar.py" || ( echo. & echo Nao consegui instalar. Confira o Python e o git. & pause & exit /b 1 )
)
"%PY%" "%AQUI%trocar_rosto_video.py" %*
echo.
pause
