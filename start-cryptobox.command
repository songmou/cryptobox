#!/usr/bin/env bash
set -uo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$project_dir" || exit 1

pause_on_error() {
  local status="$1"
  if [ "$status" -ne 0 ] && [ -t 0 ]; then
    printf '\n启动失败，按回车键关闭窗口…'
    read -r _
  fi
  exit "$status"
}

if [ "$(uname -s)" != "Darwin" ]; then
  echo "错误：start-cryptobox.command 仅适用于 macOS。" >&2
  pause_on_error 1
fi

version="$(awk -F'"' '/^[[:space:]]*version[[:space:]]*=/{print $2; exit}' pyproject.toml)"
if [ -z "$version" ]; then
  echo "错误：无法从 pyproject.toml 读取版本号。" >&2
  pause_on_error 1
fi

binary="$project_dir/dist/cryptobox-$version"
if [ ! -f "$binary" ]; then
  echo "错误：未找到当前版本的 macOS 产物：" >&2
  echo "  $binary" >&2
  echo "请先运行 scripts/build.sh 构建当前版本。" >&2
  pause_on_error 1
fi

if [ ! -x "$binary" ]; then
  chmod u+x "$binary" || {
    echo "错误：无法为 $binary 添加执行权限。" >&2
    pause_on_error 1
  }
fi

if find src cryptobox.spec pyproject.toml -type f -newer "$binary" -print -quit 2>/dev/null | grep -q .; then
  echo "警告：源码比 dist 产物新，当前程序可能不包含最近修改。"
  echo "建议更新版本号并重新运行 scripts/build.sh。"
  echo ""
fi

vault="${1:-$HOME/CryptoboxVault}"
if [ "$#" -gt 0 ]; then shift; fi

echo "启动 Cryptobox"
echo "  平台   : macOS"
echo "  版本   : $version"
echo "  入口   : $binary"
echo "  保险库 : $vault"
echo ""

"$binary" --root "$vault" "$@"
pause_on_error "$?"
