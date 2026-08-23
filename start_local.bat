@echo off
setlocal
cd /d "%~dp0"

if exist "sna-env\Scripts\python.exe" (
    echo Starting Sports News Aggregator with the local virtual environment...
    "sna-env\Scripts\python.exe" app.py
) else (
    echo Local virtual environment not found. Falling back to system Python...
    python app.py
)
