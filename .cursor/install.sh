#!/usr/bin/env bash
# Idempotent environment bootstrap for the BeinTaskBot repository.
# Safe to run repeatedly: it only installs what is missing.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── System dependencies ──────────────────────────────────────────────────────
# ffmpeg: required for Telegram voice-message transcription.
# python3.12-venv: the Ubuntu base ships python3.12 without the venv module.
need_apt=0
command -v ffmpeg >/dev/null 2>&1 || need_apt=1
dpkg -s python3.12-venv >/dev/null 2>&1 || need_apt=1
if [ "$need_apt" -eq 1 ]; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq ffmpeg python3.12-venv
fi

# ── Python virtual environment + dependencies ────────────────────────────────
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "BeinTaskBot environment ready. Activate with: source .venv/bin/activate"
