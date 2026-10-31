@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found.
    echo Create it first with: python -m venv .venv
    pause
    exit /b 1
)

start "Network Monitor Server" cmd /k ""%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8000"
