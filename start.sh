#!/usr/bin/env bash
set -e

echo "=== AGC Assurances - RCA Contract Generator ==="

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
    cp ".env.example" ".env"
fi

# Check Tesseract
if command -v tesseract >/dev/null 2>&1; then
    echo "[OK] Tesseract OCR detected."
else
    echo "[WARNING] Tesseract not found. Local OCR will be unavailable."
    echo "  Install with:  sudo apt install tesseract-ocr tesseract-ocr-fra   (Debian/Ubuntu)"
    echo "            or:  brew install tesseract tesseract-lang               (macOS)"
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment (.venv)..."
    python3 -m venv .venv
fi

echo "Activating virtual environment..."
source .venv/bin/activate

echo "Installing required packages..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "Trying optional neural OCR engine (non-fatal)..."
pip install --quiet -r requirements-ocr.txt || echo "[INFO] Neural OCR unavailable on this Python - continuing in Tesseract mode."

# Port from .env (default 8000), same as start.bat
PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' \r')
PORT=${PORT:-8000}

echo ""
echo "=========================================================="
echo " Starting AGC Assurances App on http://localhost:$PORT"
echo " Engines: Gemini cloud if key set, else 100% local OCR"
echo "=========================================================="
echo ""

uvicorn main:app --host 0.0.0.0 --port "$PORT"
