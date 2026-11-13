#!/usr/bin/env bash
# Spustenie na Linuxe / macOS. Pri prvom spustení vytvorí .venv a nainštaluje PySide6.
set -e
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
    echo "Nenašiel sa Python. Nainštalujte Python 3.10+ (https://www.python.org/downloads/)."
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "Vytváram virtuálne prostredie .venv ..."
    "$PY" -m venv .venv
fi

if ! .venv/bin/python -c "import PySide6" 2>/dev/null; then
    echo "Inštalujem PySide6 (len prvýkrát, chvíľu to trvá) ..."
    .venv/bin/python -m pip install --upgrade pip >/dev/null
    .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python app.py
