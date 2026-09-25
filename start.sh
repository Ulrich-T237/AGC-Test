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

echo ""
echo "=========================================================="
echo " Starting AGC Assurances App on http://localhost:8000"
echo " Engines: 100% local OCR (RapidOCR + Tesseract), no cloud"
echo "=========================================================="
echo ""

uvicorn main:app --host 0.0.0.0 --port 8000
