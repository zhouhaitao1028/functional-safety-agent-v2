@echo off
cd /d "%~dp0"
echo ============================================
echo   Functional Safety Agent - Web UI
echo   Browser will open http://127.0.0.1:8000
echo   Close this window to stop the server
echo ============================================
start "" cmd /c "timeout /t 5 /nobreak >nul & start "" http://127.0.0.1:8000"
python run.py web
echo.
echo Server exited. Press any key to close...
pause >nul
