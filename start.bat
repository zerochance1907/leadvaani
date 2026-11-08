@echo off
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul
start "" http://127.0.0.1:8800
py -3 app.py
pause
