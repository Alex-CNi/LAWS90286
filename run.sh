#!/usr/bin/env bash
# Start the app from the virtual environment made by setup.sh (any extra arguments go to streamlit).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "No .venv yet: run   bash setup.sh   first." >&2
  exit 1
fi
exec .venv/bin/python -m streamlit run home.py "$@"
