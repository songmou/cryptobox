#!/usr/bin/env bash
set -euo pipefail

# 平台守卫：此脚本仅适用于 macOS / Linux
OS="$(uname -s)"
case "$OS" in
  Darwin|Linux) ;;
  *)
    echo "错误：run-dev.sh 仅适用于 macOS / Linux。" >&2
    echo "在 Windows 上请使用 scripts/run-dev.ps1。" >&2
    exit 1 ;;
esac

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"

# 保险库目录：第一个参数为 --root，否则使用默认
VAULT="${1:-$HOME/cryptofile}"

# 选择入口：优先 dist 编译产物，其次 venv，最后 python -m
if [ -x "dist/cryptobox" ]; then
  CMD=(dist/cryptobox)
elif [ -x ".venv/bin/cryptobox" ]; then
  CMD=(.venv/bin/cryptobox)
elif command -v python3 >/dev/null 2>&1; then
  CMD=(python3 -m cryptobox.main)
else
  echo "错误：未找到 Python 且未构建 cryptobox，请先运行 scripts/build.sh。" >&2
  exit 1
fi

echo "启动 Cryptobox"
echo "  平台   : $OS"
echo "  入口   : ${CMD[*]}"
echo "  保险库 : $VAULT"
echo ""
exec "${CMD[@]}" --root "$VAULT"
