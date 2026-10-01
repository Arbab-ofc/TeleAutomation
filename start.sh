#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

if [[ ! -x "$BACKEND_DIR/venv/bin/python" ]]; then
  echo "Backend virtual environment missing. Run: cd backend && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "Frontend dependencies missing. Run: cd frontend && npm install"
  exit 1
fi

cleanup() {
  trap - INT TERM EXIT
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

cd "$BACKEND_DIR"
"$BACKEND_DIR/venv/bin/python" -m uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!
cd "$FRONTEND_DIR"
npm run dev -- --host 127.0.0.1 &
FRONTEND_PID=$!

echo "TELE AUTOMATION is starting"
echo "Dashboard: http://localhost:5173"
echo "API:       http://localhost:8000"
wait
