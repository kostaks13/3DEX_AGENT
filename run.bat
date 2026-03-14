@echo off
title Offline LLM CAD Komut Yorumlayicisi

set PYEXE=%~dp0python\python.exe

if not exist "%PYEXE%" (
    echo [HATA] python\python.exe bulunamadi!
    echo Embedded Python kurulumu eksik.
    pause
    exit /b 1
)

echo ========================================
echo   Offline LLM CAD Komut Yorumlayicisi
echo ========================================
echo.
echo Mod secin:
echo   1 - Simulasyon modu (3DEXPERIENCE gereksiz)
echo   2 - Gercek 3DEXPERIENCE modu (COM baglanti)
echo.
set /p choice="Seciminiz (1-2): "

if "%choice%"=="1" "%PYEXE%" "%~dp0chat.py" --debug
if "%choice%"=="2" "%PYEXE%" "%~dp0chat.py" --3dx --debug

pause
