#!/bin/bash
# Wraps PyInstaller's dist/RaceEngineer/ into "Race Engineer.app" and a .dmg to share.
#     packaging/macos/build_dmg.sh <version>
set -euo pipefail
VERSION="${1:-0.0.0}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$ROOT/dist/Race Engineer.app"
rm -rf "$APP" "$ROOT/dist/dmg"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp -R "$ROOT/dist/RaceEngineer" "$APP/Contents/Resources/engine"
cp "$ROOT/packaging/macos/launcher.sh" "$APP/Contents/MacOS/RaceEngineer"
chmod +x "$APP/Contents/MacOS/RaceEngineer"
sed "s/APP_VERSION/$VERSION/g" "$ROOT/packaging/macos/Info.plist" > "$APP/Contents/Info.plist"
# Ad-hoc signature (no Apple account): Apple Silicon Macs refuse to run unsigned code at all.
codesign --force --deep --sign - "$APP"
mkdir -p "$ROOT/dist/dmg"
cp -R "$APP" "$ROOT/dist/dmg/"
ln -s /Applications "$ROOT/dist/dmg/Applications"
cp "$ROOT/docs/telemetry-guide.md" "$ROOT/dist/dmg/Telemetry guide.txt"
cp "$ROOT/docs/mac-first-open.md" "$ROOT/dist/dmg/READ ME FIRST.txt"
hdiutil create -volname "Race Engineer" -srcfolder "$ROOT/dist/dmg" -ov -format UDZO \
  "$ROOT/dist/RaceEngineer-$VERSION-mac.dmg"
