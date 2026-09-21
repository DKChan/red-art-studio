@echo off
rem red-art-studio one-click launcher (Windows).
rem Steps: ensure uv -> install deps -> start server on :8600 -> open web UI.

where uv >nul 2>nul
if errorlevel 1 (
  echo [red-art-studio] uv not found. Install it first:
  echo   https://docs.astral.sh/uv/getting-started/installation/
  echo   or: pip install uv
  pause
  exit /b 1
)

echo [red-art-studio] syncing dependencies...
uv sync
if errorlevel 1 (
  echo [red-art-studio] uv sync failed. Check network / GoProxy and retry.
  pause
  exit /b 1
)

curl -s -o nul http://127.0.0.1:8600/healthz
if errorlevel 1 (
  echo [red-art-studio] starting server on http://127.0.0.1:8600 ...
  start "red-art-studio server" cmd /c "uv run uvicorn server.app.main:app --port 8600"
  timeout /t 3 /nobreak >nul
) else (
  echo [red-art-studio] server already running on http://127.0.0.1:8600
)

start "" http://127.0.0.1:8600
echo [red-art-studio] web UI opened. The server runs in its own window; close that window to stop it.
