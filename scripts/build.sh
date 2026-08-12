#!/usr/bin/env bash
set -euo pipefail

# 平台守卫：此脚本仅适用于 macOS / Linux
OS="$(uname -s)"
case "$OS" in
  Darwin|Linux) ;;
  *)
    echo "错误：build.sh 仅适用于 macOS / Linux。" >&2
    echo "在 Windows 上请使用 scripts/build.ps1。" >&2
    exit 1 ;;
esac

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
export PYINSTALLER_CONFIG_DIR="${TMPDIR:-/tmp}/cryptobox-pyinstaller"
cd "$project_dir"

PY="${PYTHON:-python3}"

# 若 .venv 不存在则自动创建并安装依赖
if [ ! -x ".venv/bin/python" ]; then
  echo "未检测到 .venv，正在创建虚拟环境并安装依赖..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -e ".[dev]"
fi

# 从 pyproject.toml 读取版本号，产物命名为 cryptobox-<版本>（无 .exe 后缀）
VERSION="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/')"
EXE_NAME="cryptobox-${VERSION}"

# 版本冲突检查：dist 中已存在同版本产物则中止，保留历史文件不动
if [ -f "dist/${EXE_NAME}" ]; then
  echo "错误：版本文件已存在：dist/${EXE_NAME}。请更新版本号后重新打包。" >&2
  exit 1
fi

echo "构建 Cryptobox（平台: $OS）..."
if command -v npm >/dev/null 2>&1 && [ -d node_modules ]; then
  npm run build:preview
elif [ ! -f "src/cryptobox/static/preview-host.js" ] || [ ! -f "src/cryptobox/static/THIRD_PARTY_NOTICES.txt" ]; then
  echo "错误：缺少网页预览静态包。请安装 Node.js 后运行 npm ci 和 npm run build:preview。" >&2
  exit 1
fi

# 构建到临时目录，避免清空 dist 中的历史版本
"$project_dir/.venv/bin/pyinstaller" --clean --noconfirm cryptobox.spec --distpath dist_build
if [ ! -f "dist_build/${EXE_NAME}" ]; then
  echo "错误：构建失败，未生成 dist_build/${EXE_NAME}。" >&2
  exit 1
fi

# 并入 dist，保留历史版本产物
mkdir -p dist
mv "dist_build/${EXE_NAME}" "dist/${EXE_NAME}"
rm -rf dist_build

echo "构建完成：dist/${EXE_NAME}（版本 ${VERSION}）"
