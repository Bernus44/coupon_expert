#!/usr/bin/env bash
# Bootstrap local deps (works when ~/.local/bin is not on PATH).
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Erreur: python3 introuvable. Installe Python 3 ou exporte PYTHON=/chemin/vers/python3"
  exit 1
fi

"$PYTHON" -m pip install --user -r requirements.txt
"$PYTHON" -m playwright install chromium

echo
echo "OK. Lance ensuite :"
echo "  $PYTHON main.py"
echo "  $PYTHON main.py --scrape -v"
