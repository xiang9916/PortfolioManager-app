#!/bin/bash
# Phase 8: assemble a distributable PortfolioManager.app from the SPM release build.
# Usage: scripts/build_app.sh [--with-venv] [--no-codesign] [--dmg] [--zip]
#   --dmg  产出 DMG（隐含 --zip：发布产物固定为 DMG + 仅含 .app 的 zip 两件套）
#   --zip  只产出 PortfolioManager-<version>.zip（顶层就是 PortfolioManager.app）
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root
ROOT="$(pwd)"
APP_NAME="PortfolioManager"
BUILD_DIR=".build/release"
DIST="dist"
BUNDLE="${DIST}/${APP_NAME}.app"

WITH_VENV=0
CODESIGN=1
MAKE_DMG=0
MAKE_ZIP=0
for a in "$@"; do
  case "$a" in
    --with-venv) WITH_VENV=1 ;;
    --no-codesign) CODESIGN=0 ;;
    --dmg) MAKE_DMG=1; MAKE_ZIP=1 ;;   # DMG 与 zip 是固定搭配的发布产物, 不做单选
    --zip) MAKE_ZIP=1 ;;
  esac
done

echo "==> swift build -c release"
# 必须显式传 -platform_version: SwiftPM 会把 deployment target 当成 SDK 版本写进
# LC_BUILD_VERSION (实测 minos/sdk 都是 14.0)。macOS 会据此把 app 当成"用旧 SDK 构建"
# 而启用兼容外观 —— TabView 标签栏落到标题下方、窗口标题常显, 与 0.3-beta2 (其 sdk 记录
# 为 26.5) 的观感完全不同。显式指定真实 SDK 版本后即与 beta2 一致 (minos 仍为 14.0,
# 与 Package.swift 的 .macOS(.v14) 对齐)。
MACOS_DEPLOY="14.0"
SDK_VERSION="$(xcrun --sdk macosx --show-sdk-version 2>/dev/null || true)"
if [ -z "${SDK_VERSION}" ]; then
  echo "    (warning: xcrun 取不到 SDK 版本, 退回 deployment target ${MACOS_DEPLOY})"
  SDK_VERSION="${MACOS_DEPLOY}"
fi
echo "    linking with -platform_version macos ${MACOS_DEPLOY} ${SDK_VERSION}"
# --disable-sandbox flags make it work inside nested sandboxes (e.g. DSH); harmless elsewhere.
swift build -c release --disable-sandbox \
  -Xswiftc -Xfrontend -Xswiftc -disable-sandbox \
  -Xlinker -platform_version -Xlinker macos -Xlinker "${MACOS_DEPLOY}" -Xlinker "${SDK_VERSION}"

echo "==> assembling bundle at ${BUNDLE}"
rm -rf "${BUNDLE}"
mkdir -p "${BUNDLE}/Contents/MacOS" "${BUNDLE}/Contents/Resources"

cp "${BUILD_DIR}/${APP_NAME}" "${BUNDLE}/Contents/MacOS/${APP_NAME}"
cp "scripts/Info.plist" "${BUNDLE}/Contents/Info.plist"
printf 'APPL????' > "${BUNDLE}/Contents/PkgInfo"

# 隐私: release 二进制里会带上构建机绝对路径 (/Users/<user>/Documents/Github/... 与
# .build/out/... 的 debug info 记录)。发布出去等于公开用户名与目录结构; -file-prefix-map
# 只能覆盖一部分(还会干扰 pcm 查找), 所以这里做**等长**中性替换: 只改调试元数据里的字节,
# 不动 Mach-O 任何偏移/大小, 且必须在 codesign 之前执行(改完再签名)。
echo "==> scrubbing build-machine paths inside binary"
python3 - "${BUNDLE}/Contents/MacOS/${APP_NAME}" "${HOME}" <<'PY'
import sys
binary, home = sys.argv[1], sys.argv[2].encode()
neutral = b'/tmp/pmbuild'          # 与 /Users/<user> 等长(12 字节)
data = open(binary, 'rb').read()
if len(neutral) != len(home):
    print(f'    skipped: home prefix is {len(home)} bytes, neutral is {len(neutral)}')
    sys.exit(0)
n = data.count(home)
if n:
    open(binary, 'wb').write(data.replace(home, neutral))
print(f'    replaced {n} occurrences of the build-machine home path')
PY

# Optimizer scripts (small, pure python) always bundled.
mkdir -p "${BUNDLE}/Contents/Resources/Optimizer"
cp -R "Optimizer/scripts" "${BUNDLE}/Contents/Resources/Optimizer/scripts"
rm -rf "${BUNDLE}/Contents/Resources/Optimizer/scripts/__pycache__"

# Optimizer static data (calibrated_params.json etc.) as bundle fallback for
# params._resolve_data_file (env DSH_FINANCE_DIR -> dev Finance/tmp -> here).
if [ -d "Optimizer/data" ]; then
  cp -R "Optimizer/data" "${BUNDLE}/Contents/Resources/Optimizer/data"
fi

# One-time purge marker: if present in bundle, app purges all numbers-sourced
# assets on first launch (only once per install, gated by .purge_done in App Support).
cp "Resources/purge_request.txt" "${BUNDLE}/Contents/Resources/purge_request.txt"

# Icon: generate PNG + icns.
echo "==> generating icon"
swift "scripts/make_icon.swift" "${DIST}/AppIcon.png"
ICONSET="${DIST}/AppIcon.iconset"
rm -rf "${ICONSET}"; mkdir -p "${ICONSET}"
for s in 16 32 64 128 256 512; do
  sips -z ${s} ${s} "${DIST}/AppIcon.png" --out "${ICONSET}/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "${DIST}/AppIcon.png" --out "${ICONSET}/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "${ICONSET}" -o "${BUNDLE}/Contents/Resources/AppIcon.icns"

# Optionally bundle the Python venv (relocatable only on this machine: symlinks
# point at /opt/miniconda3 which must exist).
if [ "${WITH_VENV}" = "1" ]; then
  echo "==> bundling venv (this-machine relocatable)"
  rm -rf "${BUNDLE}/Contents/Resources/Optimizer/.venv"
  cp -R "Optimizer/.venv" "${BUNDLE}/Contents/Resources/Optimizer/.venv"

  # 隐私: 拷进来的 venv 里到处是构建机绝对路径（bin/activate、pyvenv.cfg 的 command 行、
  # __pycache__ 里 .pyc 嵌的源码路径）。统一重写成 /build 并删掉字节码缓存 —— 运行时只直接
  # 执行 bin/python3（真正的二进制），不依赖这些文本里的路径，删缓存只会让首跑重新编译。
  echo "==> scrubbing build-machine paths inside venv"
  VENV="${BUNDLE}/Contents/Resources/Optimizer/.venv"
  find "${VENV}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  # 注意: 不要写成 `grep ... | while ... done` —— venv 里已经没有 /Users 引用时 grep 返回 1,
  # 配合脚本开头的 set -o pipefail 会把整个打包脚本在这里中止（实测踩过：无 DMG/无 zip/未签名）。
  # 先把命中列表落盘再处理, 列表为空就跳过。
  SCRUB_LIST="${DIST}/.venv-scrub-list"
  if grep -rlI -E '/Users/[A-Za-z0-9_.-]+/' "${VENV}" >"${SCRUB_LIST}" 2>/dev/null; then
    while IFS= read -r f; do
      perl -pi -e 's#/Users/[A-Za-z0-9_.-]+/#/build/#g' "$f"
    done <"${SCRUB_LIST}"
  fi
  rm -f "${SCRUB_LIST}"
  echo "    remaining /Users references: $(grep -rlI -E '/Users/[A-Za-z0-9_.-]+/' "${VENV}" 2>/dev/null | wc -l | tr -d ' ')"
fi

if [ "${CODESIGN}" = "1" ]; then
  echo "==> ad-hoc codesign"
  codesign --force --deep --sign - "${BUNDLE}"
  echo "==> verify"
  codesign --verify --verbose=2 "${BUNDLE}"
fi

# 发布产物固定为两件套: DMG（拖拽安装, 带 /Applications 快捷方式）+ 仅含 .app 的 zip。
if [ "${MAKE_DMG}" = "1" ] || [ "${MAKE_ZIP}" = "1" ]; then
  VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${BUNDLE}/Contents/Info.plist")"
fi

if [ "${MAKE_DMG}" = "1" ]; then
  echo "==> creating dmg"
  DMG="${DIST}/PortfolioManager-${VERSION}.dmg"
  STAGE="${DIST}/dmg-staging"
  rm -rf "${STAGE}" "${DMG}"
  mkdir -p "${STAGE}"
  cp -R "${BUNDLE}" "${STAGE}/"
  ln -s /Applications "${STAGE}/Applications"
  # 不再放「一键安装.command」: 它自身同样带隔离标记、双击第一次仍会被 Gatekeeper 拦,
  # 收益为零却多一个要维护的脚本 —— 镜像里只留 app + /Applications 快捷方式, 拖进去即可。
  VOLNAME="投资组合管家 ${VERSION}"
  # 必须显式 -fs HFS+: 新版 macOS 下 hdiutil create 默认生成 APFS 镜像, 同样内容
  # 压缩后比 HFS+ 大约 50% (实测 261MB bundle: APFS 130MB vs HFS+ 86MB)。以前在
  # DSH 沙箱里 hdiutil create 会失败并走下面的 makehybrid 回退 (产出 HFS+/ISO),
  # 所以旧版反而更小; 一旦 create 成功就会得到臃肿的 APFS 镜像 —— 故在此固定 HFS+。
  # Preferred: compressed UDZO in one step. Fails in some sandboxes
  # (newfs_apfs: Operation not permitted) -> fall back to makehybrid
  # (HFS+/ISO hybrid) then convert to compressed UDZO.
  if ! hdiutil create -volname "${VOLNAME}" -srcfolder "${STAGE}" -ov -format UDZO -fs HFS+ "${DMG}" 2>/dev/null; then
    echo "==> hdiutil create blocked, falling back to makehybrid + convert"
    rm -f "${DMG}"
    # hdiutil convert recognizes images by extension -> keep .dmg.
    RAW="${DIST}/hybrid-${VERSION}.dmg"
    rm -f "${RAW}" "${RAW}.iso"
    hdiutil makehybrid -o "${RAW}" -hfs -iso -joliet \
      -default-volume-name "${VOLNAME}" -hfs-volume-name "${VOLNAME}" "${STAGE}"
    # makehybrid may append .iso to the output name
    if [ -f "${RAW}.iso" ]; then mv "${RAW}.iso" "${RAW}"; fi
    hdiutil convert "${RAW}" -format UDZO -ov -o "${DMG}"
    rm -f "${RAW}"
  fi
  rm -rf "${STAGE}"
  hdiutil verify "${DMG}" >/dev/null 2>&1 || echo "    (verify skipped: hybrid-derived image has no checksum)"
  echo "==> dmg: ${DMG} ($(du -h "${DMG}" | cut -f1))"
fi

if [ "${MAKE_ZIP}" = "1" ]; then
  echo "==> creating zip (only the .app)"
  ZIP="${DIST}/PortfolioManager-${VERSION}.zip"
  rm -f "${ZIP}"
  # 用 ditto 而不是 zip: 它是 macOS 上打 app 包的正解 —— 保留符号链接
  # (venv/bin/python3)、可执行位与扩展属性, 解压回来签名封条仍然完整。
  # --keepParent 保证 zip 顶层是 PortfolioManager.app 本身（而不是它里面的内容）。
  # 不加 --sequesterRsrc: 它会额外产出顶层 __MACOSX/（AppleDouble 元数据）,
  # 与"zip 里只有 .app"的约定不符; 实测去掉后符号链接/可执行位/封条全部完好。
  ditto -c -k --keepParent "${BUNDLE}" "${ZIP}"
  echo "==> zip: ${ZIP} ($(du -h "${ZIP}" | cut -f1))"
fi

echo "==> done: ${BUNDLE}"
du -sh "${BUNDLE}"
