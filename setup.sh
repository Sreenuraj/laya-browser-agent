#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "  Laya Browser Agent Autonomous Agent - Environment Setup"
echo "=========================================================="

# 1. Inspect Machine Architecture
OS="$(uname -s)"
ARCH="$(uname -m)"

echo "[1/5] Inspecting machine hardware..."
echo "      Operating System: $OS"
echo "      Architecture:     $ARCH"

if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
    echo "      ✓ Apple Silicon detected! Native Metal GPU acceleration enabled."
else
    echo "      i Host is $OS ($ARCH). Decision loop will run via local LLM / CPU runner."
fi

# 2. Check Python
echo "[2/5] Checking Python installation..."
PYTHON_BIN=""
for py in python3.12 python3.11 python3; do
    if command -v "$py" >/dev/null 2>&1; then
        VER="$($py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")"
        MAJOR="$(echo $VER | cut -d. -f1)"
        MINOR="$(echo $VER | cut -d. -f2)"
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 11 ]; then
            PYTHON_BIN="$py"
            echo "      ✓ Found compatible Python: $PYTHON_BIN (v$VER)"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "      ✗ Error: Python 3.11+ is required."
    exit 1
fi

# 3. Create virtual environment
echo "[3/5] Setting up virtual environment..."
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r requirements.txt --quiet
if [ -f "requirements-dev.txt" ]; then
    "$VENV_DIR/bin/pip" install -r requirements-dev.txt --quiet
fi
echo "      ✓ Dependencies installed successfully."

# 4. Install Playwright Chromium
echo "[4/5] Provisioning Playwright Chromium browser..."
if [ "$OS" = "Linux" ]; then
    "$VENV_DIR/bin/playwright" install --with-deps chromium 2>/dev/null || "$VENV_DIR/bin/playwright" install chromium
else
    "$VENV_DIR/bin/playwright" install chromium
fi
echo "      ✓ Chromium browser binary is ready."

# 5. Inspect Model & Ollama
echo "[5/5] Verifying models..."
if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
    "$VENV_DIR/bin/python" -c "
from pathlib import Path
cache_dir = Path.home() / '.cache' / 'huggingface' / 'hub' / 'models--aac6fef--laya-mlx'
if cache_dir.exists():
    print('      ✓ Laya MLX model found in local cache!')
else:
    try:
        print('      i Downloading Laya MLX model weights...')
        from laya_mlx import Agent
        Agent('aac6fef/laya-mlx')
        print('      ✓ Laya model downloaded and cached.')
    except Exception as e:
        print(f'      ! Notice: Could not pre-cache Laya MLX model: {e}')
"
else
    echo "      i Non-Apple Silicon host. Local decision policy runs via local LLM / Ollama runner."
fi

if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "      ✓ Local Ollama service detected on port 11434 (zero-cost parameter extraction enabled)."
else
    echo "      i Ollama is not running. Deterministic regex slot extraction will be used."
fi

echo "=========================================================="
echo "  Setup Complete! Launch the agent with:"
echo "      ./run.sh"
echo "=========================================================="
