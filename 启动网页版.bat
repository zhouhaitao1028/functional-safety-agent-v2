@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   功能安全智能体 - 网页版
echo   启动后浏览器将自动打开 http://127.0.0.1:8000
echo   关闭本窗口即停止服务
echo ============================================
start "" cmd /c "timeout /t 5 /nobreak >nul & start "" http://127.0.0.1:8000"
python run.py web
echo.
echo 服务已退出，按任意键关闭窗口...
pause >nul
