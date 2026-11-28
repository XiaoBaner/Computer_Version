@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0tools\avatar_preview.py" --open %*
    goto done
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0tools\avatar_preview.py" --open %*
    goto done
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
    python "%~dp0tools\avatar_preview.py" --open %*
    goto done
)
echo Install Python 3.11 or newer, or create a project .venv.
pause
exit /b 1

:done
set "avatar_status=%ERRORLEVEL%"
if not "%avatar_status%"=="0" pause
exit /b %avatar_status%
