#!/usr/bin/env bash
# One-click start for macOS / Linux: creates a virtual environment on first run, then launches the app.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
else
  source .venv/bin/activate
fi
python run.py "$@"
