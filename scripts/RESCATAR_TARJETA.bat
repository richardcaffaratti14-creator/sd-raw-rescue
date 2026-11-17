@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Rescate de fotos RAW de tarjeta SD (solo lectura)
cd /d "%~dp0"

rem --- pide permisos de administrador (hacen falta para leer la tarjeta cruda)
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Pidiendo permisos de administrador...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

rem --- busca Python 3
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY ( python --version >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo No encuentro Python 3. Instalalo desde https://www.python.org/downloads/ ^(tildar "Add python.exe to PATH"^) y volve a abrir este archivo.
  pause
  exit /b 1
)
if not exist "%~dp0logs" mkdir "%~dp0logs"

:menu
cls
echo ================================================================
echo   RESCATE DE FOTOS RAW DE UNA TARJETA SD  -  SOLO LECTURA
echo   (nunca escribe en la tarjeta)
echo ================================================================
echo.
echo   1. Diagnostico de la tarjeta (no copia nada)
echo   2. Hacer una imagen completa de la tarjeta (hacer esto primero)
echo   3. Extraer las fotos RAW y JPG que se puedan
echo   4. Sacar los JPEG incrustados de RAW ya recuperados
echo   5. Salir
echo.
set "OP="
set /p OP=Elegi una opcion:
if "%OP%"=="1" goto diag
if "%OP%"=="2" goto imagen
if "%OP%"=="3" goto extraer
if "%OP%"=="4" goto thumbs
if "%OP%"=="5" exit /b 0
goto menu

:diag
set /p L=Letra de la tarjeta (ej. E):
set "L=%L::=%"
%PY% "%~dp0rescatar_sd.py" scan %L%: --log "%~dp0logs\diagnostico.txt" --pause
goto menu

:imagen
set /p L=Letra de la tarjeta (ej. E):
set "L=%L::=%"
set /p D=Carpeta de destino (necesita lugar libre igual al tamano de la tarjeta, ej. D:\RESCATE):
if not exist "%D%" mkdir "%D%"
%PY% "%~dp0rescatar_sd.py" image %L%: --out "%D%\tarjeta.img" --log "%~dp0logs\imagen.txt" --pause
goto menu

:extraer
echo Origen: letra de la tarjeta (E) o ruta de una imagen hecha con la opcion 2 (ej. D:\RESCATE\tarjeta.img)
set /p L=Origen:
set /p D=Carpeta donde dejar las fotos (ej. D:\RESCATE\FOTOS):
if not exist "%D%" mkdir "%D%"
set "SRC=%L%"
if /i "%L:~1,1%"=="" set "SRC=%L%:"
%PY% "%~dp0rescatar_sd.py" extract "%SRC%" --out "%D%" --log "%~dp0logs\extraccion.txt" --pause
goto menu

:thumbs
set /p P=Carpeta con los RAW (ej. D:\RESCATE\FOTOS):
set /p D=Carpeta donde dejar los JPEG (ej. D:\RESCATE\JPEG):
%PY% "%~dp0rescatar_sd.py" thumbs "%P%" --out "%D%" --log "%~dp0logs\miniaturas.txt" --pause
goto menu
