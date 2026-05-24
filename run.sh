#!/bin/zsh
cd "$(dirname "$0")"
PYTHON=".venv/bin/python3.12"
if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: $PYTHON not found. Set up the venv first:" >&2
    echo "  python3.12 -m venv .venv && .venv/bin/pip install PyQt6 PyOpenGL sounddevice numpy" >&2
    exit 1
fi
exec "$PYTHON" main.py "$@"
