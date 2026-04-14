@echo off
setlocal

echo.
echo  PolySignal — Demo Setup
echo  =======================
echo.

REM ── Check .env exists ──────────────────────────────────────────────────────
if not exist ".env" (
    echo  [ERROR] .env file not found!
    echo  Copy .env.example to .env and fill in your API keys.
    echo.
    echo    copy .env.example .env
    echo    notepad .env
    echo.
    pause
    exit /b 1
)

REM ── Create venv if it doesn't exist ────────────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo  [SETUP] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] python -m venv failed. Is Python 3.10+ installed?
        pause
        exit /b 1
    )
    echo  [SETUP] Virtual environment created.
    echo.
)

REM ── Install / upgrade dependencies ─────────────────────────────────────────
echo  [SETUP] Installing dependencies (this takes ~60s on first run)...
.venv\Scripts\pip install -q -r requirements.txt
if errorlevel 1 (
    echo  [ERROR] pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo  [SETUP] Dependencies OK.
echo.

REM ── Quick smoke-test ────────────────────────────────────────────────────────
echo  [CHECK] Running smoke tests...
.venv\Scripts\python.exe -X utf8 -m pytest tests/ -q --tb=short 2>&1
if errorlevel 1 (
    echo.
    echo  [WARN] Some tests failed — server may still work for demo.
    echo  Press any key to start the server anyway, or Ctrl+C to abort.
    pause
)
echo.

REM ── Start server ────────────────────────────────────────────────────────────
echo  [START] Launching PolySignal at http://localhost:8000
echo  Press Ctrl+C to stop.
echo.
.venv\Scripts\python.exe -X utf8 -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload

endlocal
