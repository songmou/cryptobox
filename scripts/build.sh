#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
export PYINSTALLER_CONFIG_DIR="${TMPDIR:-/tmp}/cryptobox-pyinstaller"
cd "$project_dir"
exec "$project_dir/.venv/bin/pyinstaller" --clean --noconfirm cryptobox.spec

