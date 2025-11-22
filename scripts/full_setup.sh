#!/usr/bin/env bash
# One-shot setup script to install Python + Node dependencies, build, and launch the packaged Electron app.
# Usage: ./scripts/full_setup.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN=${PYTHON:-python3}

echo "[setup] Creating virtual environment (.venv) with ${PYTHON_BIN}" 
if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
source .venv/bin/activate

echo "[setup] Upgrading pip"
python -m pip install --upgrade pip

echo "[setup] Installing Python dependencies"
python -m pip install -r sora_suite/requirements.txt

if command -v python >/dev/null 2>&1; then
  echo "[setup] Ensuring Playwright Chromium is installed"
  python -m playwright install chromium
fi

echo "[setup] Installing Node/Electron dependencies"
npm install

echo "[setup] Building renderer + Electron main"
npm run build

echo "[setup] Launching packaged desktop app"
npm run app:dist
