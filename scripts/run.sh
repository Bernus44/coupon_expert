#!/usr/bin/env bash
# Convenience wrapper: python3 main.py "$@"
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 main.py "$@"
