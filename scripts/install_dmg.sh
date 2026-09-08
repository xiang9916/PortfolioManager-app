#!/bin/bash
# 一键安装/更新 PortfolioManager（绕过 Gatekeeper 警告）
# 两种用法:
#   1. 在 DMG 里双击「一键安装.command」—— 安装同卷里的 PortfolioManager.app
#   2. 终端运行: scripts/install_dmg.sh [dist/PortfolioManager-X.Y.dmg]
#      不带参数时自动选 dist/ 里最新的 DMG。
# 原理: 把 app 拷贝到 /Applications 并移除 com.apple.quarantine 隔离标记。
#   本地安装的 app 不再受 Gatekeeper 校验，无需每次"仍要打开"。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 1) 优先: DMG 场景 —— app 就在脚本旁边
APP_SRC=""
for c in "$SCRIPT_DIR"/*.app; do
  [ -d "$c" ] && APP_SRC="$c" && break
done

# 2) 回退: 终端场景 —— 从仓库 dist/ 挂载最新 DMG
MOUNT=""
cleanup() {
  if [ -n "$MOUNT" ]; then
    hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || true
    rmdir "$MOUNT" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [ -z "$APP_SRC" ]; then
  DIST="$SCRIPT_DIR/dist"
  DMG="${1:-}"
  if [ -z "$DMG" ]; then
    DMG=$(ls -t "$DIST"/PortfolioManager-*.dmg 2>/dev/null | head -1) || true
  fi
  [ -n "$DMG" ] && [ -f "$DMG" ] || { echo "错误: 没有找到 .app 或 DMG"; exit 1; }
  echo "==> 使用: $DMG"
  MOUNT=$(mktemp -d /tmp/pm_install.XXXXXX)
  hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MOUNT" >/dev/null
  for c in "$MOUNT"/*.app; do
    [ -d "$c" ] && APP_SRC="$c" && break
  done
fi
[ -n "$APP_SRC" ] || { echo "错误: 没有找到 PortfolioManager.app"; exit 1; }
APP_NAME="$(basename "$APP_SRC")"

echo "==> 拷贝到 /Applications（旧版本会被覆盖）"
rm -rf "/Applications/$APP_NAME"
ditto "$APP_SRC" "/Applications/$APP_NAME"

echo "==> 移除隔离标记 (com.apple.quarantine)"
xattr -dr com.apple.quarantine "/Applications/$APP_NAME" 2>/dev/null || true

echo "==> 校验签名"
codesign --verify --quiet "/Applications/$APP_NAME" && echo "    签名 OK (ad-hoc)"

echo "==> 启动"
open "/Applications/$APP_NAME"
echo "完成: 已安装并启动 ${APP_NAME%.app}，app 本体不会再弹安全警告。"
