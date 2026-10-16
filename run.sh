#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/.venv" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -d "/Users/sreenuraj/laya-test/laya-env" ]; then
    source "/Users/sreenuraj/laya-test/laya-env/bin/activate"
fi

python "$SCRIPT_DIR/main.py" "$@"
