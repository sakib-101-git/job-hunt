@echo off
REM JobHunt scrape -> filter -> score -> Telegram notify cycle.
REM Run manually by double-clicking, or via the scheduled task.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m src.main
