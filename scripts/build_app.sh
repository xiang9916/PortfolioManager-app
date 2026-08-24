#!/bin/bash
# Phase 8: assemble a distributable PortfolioManager.app from the SPM release build.
# Usage: scripts/build_app.sh [--with-venv] [--no-codesign]
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
for a in "$@"; do
  case "$a" in
    --with-venv) WITH_VENV=1 ;;
    --no-codesign) CODESIGN=0 ;;
    --dmg) MAKE_DMG=1 ;;
  esac
done

echo "==> swift build -c release"
# --disable-sandbox flags make it work inside nested sandboxes (e.g. DSH); harmless elsewhere.
swift build -c release --disable-sandbox -Xswiftc -Xfrontend -Xswiftc -disable-sandbox

echo "==> assembling bundle at ${BUNDLE}"
rm -rf "${BUNDLE}"
mkdir -p "${BUNDLE}/Contents/MacOS" "${BUNDLE}/Contents/Resources"

cp "${BUILD_DIR}/${APP_NAME}" "${BUNDLE}/Contents/MacOS/${APP_NAME}"
cp "scripts/Info.plist" "${BUNDLE}/Contents/Info.plist"
printf 'APPL????' > "${BUNDLE}/Contents/PkgInfo"

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
fi

if [ "${CODESIGN}" = "1" ]; then
  echo "==> ad-hoc codesign"
  codesign --force --deep --sign - "${BUNDLE}"
  echo "==> verify"
  codesign --verify --verbose=2 "${BUNDLE}"
fi

if [ "${MAKE_DMG}" = "1" ]; then
  echo "==> creating dmg"
  VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${BUNDLE}/Contents/Info.plist")"
  DMG="${DIST}/PortfolioManager-${VERSION}.dmg"
  STAGE="${DIST}/dmg-staging"
  rm -rf "${STAGE}" "${DMG}"
  mkdir -p "${STAGE}"
  cp -R "${BUNDLE}" "${STAGE}/"
  ln -s /Applications "${STAGE}/Applications"
  VOLNAME="投资组合管家 ${VERSION}"
  # Preferred: compressed UDZO in one step. Fails in some sandboxes
  # (newfs_apfs: Operation not permitted) -> fall back to makehybrid
  # (HFS+/ISO hybrid) then convert to compressed UDZO.
  if ! hdiutil create -volname "${VOLNAME}" -srcfolder "${STAGE}" -ov -format UDZO "${DMG}" 2>/dev/null; then
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
  echo "==> dmg: ${DMG}"
fi

echo "==> done: ${BUNDLE}"
du -sh "${BUNDLE}"
