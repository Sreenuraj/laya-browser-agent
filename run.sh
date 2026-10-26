#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# 1. Pre-check: Ensure Python 3.11+ virtual environment exists
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "[Laya] Local virtual environment not found. Initializing .venv..."
    PYTHON_BIN=""
    for py in python3.12 python3.11 python3; do
        if command -v "$py" >/dev/null 2>&1; then
            if "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
                PYTHON_BIN="$py"
                break
            fi
        fi
    done

    if [ -z "$PYTHON_BIN" ]; then
        echo "[Laya] Error: Python 3.11+ is required but not found in PATH." >&2
        exit 1
    fi

    echo "[Laya] Creating virtual environment using $PYTHON_BIN..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip --quiet 2>/dev/null || true
fi

# 2. Pre-check: Verify core Python dependencies
if ! "$VENV_DIR/bin/python" -c "
import sys, platform
import playwright, rich, httpx, pydantic
if platform.system() == 'Darwin' and platform.machine() == 'arm64':
    import laya_mlx
" 2>/dev/null; then
    echo "[Laya] Installing / syncing dependencies from requirements.txt..."
    "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
fi

# 3. Pre-check: Verify Playwright Chromium browser binary
if ! "$VENV_DIR/bin/python" -c "import sys; from pathlib import Path; from playwright.sync_api import sync_playwright; p = sync_playwright().start(); path = Path(p.chromium.executable_path); p.stop(); sys.exit(0 if path.exists() else 1)" 2>/dev/null; then
    echo "[Laya] Installing Playwright Chromium browser binary..."
    "$VENV_DIR/bin/playwright" install chromium
fi

# 4. Optional Pre-check: Pre-cache Laya MLX model on Apple Silicon if not present
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    "$VENV_DIR/bin/python" -c "
from pathlib import Path
cache_dir = Path.home() / '.cache' / 'huggingface' / 'hub' / 'models--aac6fef--laya-mlx'
if not cache_dir.exists():
    try:
        from laya_mlx import Agent
        print('[Laya] Pre-caching local Laya MLX model weights on Metal GPU...')
        Agent('aac6fef/laya-mlx')
    except Exception:
        pass
" 2>/dev/null || true
fi

# 5. Launch Laya Browser Agent CLI
exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/main.py" "$@"
