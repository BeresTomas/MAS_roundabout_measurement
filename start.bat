@echo off
rem Spustenie na Windows: dvojklik. Pri prvom spusteni vytvori .venv a nainstaluje PySide6.
setlocal
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
    echo Nenasiel sa Python. Nainstalujte Python 3.10+ z https://www.python.org/downloads/
    echo a pri instalacii zaskrtnite "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Vytvaram virtualne prostredie .venv ...
    %PY% -m venv .venv || goto :fail
)

".venv\Scripts\python.exe" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo Instalujem PySide6 (len prvykrat, chvilu to trva) ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)

".venv\Scripts\python.exe" app.py
if errorlevel 1 (
    echo.
    echo Aplikacia skoncila s chybou. Podrobnosti su vyssie a v subore merac_error.log.
    pause
)
exit /b 0

:fail
echo.
echo Instalacia zlyhala. Skontrolujte pripojenie na internet a verziu Pythonu (3.10 - 3.13 odporucane).
pause
exit /b 1
