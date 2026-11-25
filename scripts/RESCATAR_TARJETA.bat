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
set "RUN="

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
call :carpeta "%L%"
%PY% "%~dp0rescatar_sd.py" scan %L%: --log "!RUN!\logs\diagnostico.txt" --pause
goto menu

:imagen
set /p L=Letra de la tarjeta (ej. E):
set "L=%L::=%"
call :carpeta "%L%"
call :sin_pisar
if not exist "!RUN!\imagen" mkdir "!RUN!\imagen"
echo La imagen necesita lugar libre igual al tamano de la tarjeta.
%PY% "%~dp0rescatar_sd.py" image %L%: --out "!RUN!\imagen\tarjeta.img" --log "!RUN!\logs\imagen.txt" --pause
goto menu

:extraer
echo Origen: letra de la tarjeta (E) o ruta de una imagen. Enter = la imagen de este rescate.
set "L="
set /p L=Origen:
set "T="
if defined L if "!L:~2!"=="" set "T=!L:~0,1!"
call :carpeta "!T!"
if not defined L set "L=!RUN!\imagen\tarjeta.img"
set "SRC=!L!"
if "!L:~1,1!"=="" set "SRC=!L!:"
%PY% "%~dp0rescatar_sd.py" extract "!SRC!" --out "!RUN!\RECUPERADO_FINAL\FOTOS" --log "!RUN!\logs\extraccion.txt" --pause
goto menu

:thumbs
echo Carpeta con los RAW. Enter = las fotos extraidas en este rescate.
set "P="
set /p P=Carpeta:
call :carpeta ""
if not defined P set "P=!RUN!\RECUPERADO_FINAL\FOTOS"
%PY% "%~dp0rescatar_sd.py" thumbs "!P!" --out "!RUN!\RECUPERADO_FINAL\JPEG" --log "!RUN!\logs\miniaturas.txt" --pause
goto menu

rem --- una sola carpeta principal (sd-raw-rescue) y adentro una por dia; %1 = letra de la tarjeta.
rem --- Si ya hay rescates de hoy, sigue en el ultimo. Nunca la crea en la tarjeta.
:carpeta
if defined RUN exit /b 0
set "BASE=%USERPROFILE%\sd-raw-rescue"
if exist D:\ if /i not "%~1"=="D" set "BASE=D:\sd-raw-rescue"
if defined SDRAW_BASE set "BASE=%SDRAW_BASE%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "HOY=%%i"
set "RUN=!BASE!\!HOY!"
for /l %%n in (2,1,99) do if exist "!BASE!\!HOY!_%%n\" set "RUN=!BASE!\!HOY!_%%n"
if not exist "!RUN!\logs" mkdir "!RUN!\logs"
echo Carpeta de este rescate: !RUN!
exit /b 0

rem --- si la carpeta ya tiene una imagen (otra tarjeta u otra pasada), abre la siguiente: _2, _3...
:sin_pisar
if not exist "!RUN!\imagen\tarjeta.img" exit /b 0
set "N=1"
:sin_pisar_sig
set /a N+=1
if exist "!BASE!\!HOY!_!N!\" goto sin_pisar_sig
set "RUN=!BASE!\!HOY!_!N!"
mkdir "!RUN!\logs"
echo Ya habia una imagen. Carpeta nueva: !RUN!
exit /b 0
