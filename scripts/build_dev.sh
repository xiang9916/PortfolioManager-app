#!/usr/bin/env bash
# 开发 / 预览用构建：debug 可执行，但打上与发布包**相同**的真实 SDK 版本戳。
#
# 为什么不能直接 `swift build`：SwiftPM 会把 deployment target 当成 SDK 版本写进
# LC_BUILD_VERSION（实测 minos/sdk 都是 14.0），macOS 据此判定「用旧 SDK 构建」并启用
# 兼容外观 —— TabView 标签栏落到标题下方、窗口标题常显，与发布包观感完全不同。
# 于是「本地预览好看/难看」与用户实际看到的不是一回事，截图核对也就失去意义。
# 这里显式传 -platform_version，与 scripts/build_app.sh 保持一字不差。
#
# 用法：
#   bash scripts/build_dev.sh                      # 构建 debug 可执行
#   bash scripts/build_dev.sh --run                # 构建后在仓库根目录运行（走 tmp/portfolio.db 开发库）
set -euo pipefail
cd "$(dirname "$0")/.."

MACOS_DEPLOY="14.0"
SDK_VERSION="$(xcrun --sdk macosx --show-sdk-version 2>/dev/null || true)"
if [ -z "${SDK_VERSION}" ]; then
  echo "    (warning: xcrun 取不到 SDK 版本, 退回 deployment target ${MACOS_DEPLOY})"
  SDK_VERSION="${MACOS_DEPLOY}"
fi
echo "==> swift build (debug) -platform_version macos ${MACOS_DEPLOY} ${SDK_VERSION}"
# --disable-sandbox 系列参数让它在嵌套沙箱（如 DSH）里也能跑；在别处无害。
swift build --disable-sandbox \
  -Xswiftc -Xfrontend -Xswiftc -disable-sandbox \
  -Xlinker -platform_version -Xlinker macos -Xlinker "${MACOS_DEPLOY}" -Xlinker "${SDK_VERSION}"

BIN=".build/debug/PortfolioManager"
echo "==> 校验 ${BIN} 的 LC_BUILD_VERSION（sdk 必须等于 ${SDK_VERSION}）"
otool -l "${BIN}" | grep -A4 LC_BUILD_VERSION | grep -E "minos|sdk" | sed 's/^/    /'

if [ "${1:-}" = "--run" ]; then
  echo "==> 运行（开发库: tmp/portfolio.db）"
  exec "${BIN}"
fi
