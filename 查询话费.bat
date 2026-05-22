@echo off
chcp 65001 >nul
cd /d "%~dp0"
python telecom_query.py
echo.
pause
