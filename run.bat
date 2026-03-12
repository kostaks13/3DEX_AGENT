@echo off
title Offline LLM

set PYEXE=%~dp0python\python.exe

if not exist "%PYEXE%" (
    echo [HATA] python\python.exe bulunamadi!
    pause
    exit /b 1
)

echo ========================================
echo   Offline LLM
echo ========================================
echo.
echo Mod secin:
echo   1 - GUI Arayuz (onerilen)
echo   2 - CLI Asistan
echo.
set /p choice="Seciminiz (1-2): "

if "%choice%"=="1" "%PYEXE%" "%~dp0gui.py"
if "%choice%"=="2" "%PYEXE%" "%~dp0app.py"

pause
