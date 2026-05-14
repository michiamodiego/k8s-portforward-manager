@echo off
REM Avvia pf_manager.py usando il venv locale.
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%venv"
set "VENV_PY=%VENV_DIR%\Scripts\pythonw.exe"
if not exist "%VENV_PY%" set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo Venv non trovato. Creazione in corso...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Errore nella creazione del venv.
        pause
        exit /b 1
    )
    echo Installazione dipendenze da requirements.txt...
    "%VENV_DIR%\Scripts\python.exe" -m pip install -r "%SCRIPT_DIR%requirements.txt"
    if errorlevel 1 (
        echo Errore nell'installazione delle dipendenze.
        pause
        exit /b 1
    )
    echo Setup completato con successo.
    set "VENV_PY=%VENV_DIR%\Scripts\pythonw.exe"
    if not exist "%VENV_PY%" set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
)
start "" "%VENV_PY%" "%SCRIPT_DIR%pf_manager.py"
endlocal
