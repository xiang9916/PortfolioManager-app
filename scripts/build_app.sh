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
for a in "$@"; do
  case "$a" in
    --with-venv) WITH_VENV=1 ;;
    --no-codesign) CODESIGN=0 ;;
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

echo "==> done: ${BUNDLE}"
du -sh "${BUNDLE}"
