#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/beintaskbot-main"
exec python3 bot.py
