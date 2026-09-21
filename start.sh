#!/usr/bin/env sh
# red-art-studio one-click launcher (Linux/macOS).
# Steps: ensure uv -> install deps -> start server on :8600 -> open web UI.

if ! command -v uv >/dev/null 2>&1; then
  echo "[red-art-studio] uv not found. Install it first: https://docs.astral.sh/uv/"
  exit 1
fi

echo "[red-art-studio] syncing dependencies..."
uv sync || exit 1

if curl -sf http://127.0.0.1:8600/healthz >/dev/null 2>&1; then
  echo "[red-art-studio] server already running on http://127.0.0.1:8600"
else
  echo "[red-art-studio] starting server on http://127.0.0.1:8600 ..."
  uv run uvicorn server.app.main:app --port 8600 &
  sleep 3
fi

# open browser (best effort; server keeps running in background)
xdg-open http://127.0.0.1:8600 >/dev/null 2>&1 || open http://127.0.0.1:8600 >/dev/null 2>&1 || true
echo "[red-art-studio] web UI: http://127.0.0.1:8600"
