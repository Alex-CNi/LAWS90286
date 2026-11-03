#!/usr/bin/env bash
# Set up the app in its own virtual environment (.venv): run once, from the repository root.
#   bash setup.sh            macOS, Linux, Codespaces
# Afterwards, start the app with:  bash run.sh    (or: source .venv/bin/activate && streamlit run home.py)
set -uo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv with $("$PY" --version 2>&1)…"
  "$PY" -m venv .venv || exit 1
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
if ! .venv/bin/python -m pip install -r requirements.txt; then
  # The two ChromaDB lines are optional (requirements.txt says so): a Python with no pysqlite3-binary or chromadb
  # build gets the rest, and the app searches with NumPy instead (the same ranking in practice).
  echo
  echo "The full install failed (see above); installing without the optional ChromaDB lines…"
  grep -v -E '^(chromadb|pysqlite3-binary)' requirements.txt > .venv/requirements-core.txt
  .venv/bin/python -m pip install -r .venv/requirements-core.txt || exit 1
  echo "Installed without ChromaDB: the app uses NumPy search (Streamlit Cloud installs the full file)."
fi
.venv/bin/python -m pip install pytest >/dev/null
echo
echo "Ready ($(.venv/bin/python --version 2>&1)). Start the app with:  bash run.sh"
echo "Run the tests with:  .venv/bin/python -m pytest -q"
