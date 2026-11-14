#!/bin/bash
# Builds /Applications/VideoGrabber.app pointing at this checkout.
# Re-run after moving the repo or switching Python.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
APP="${APP:-/Applications/VideoGrabber.app}"

# Pick a python3 that has tkinter (override with PYTHON=/path/to/python3).
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
    if [ -x "$c" ] && "$c" -c 'import tkinter' 2>/dev/null; then PY="$c"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "No python3 with tkinter found. Try: brew install python python-tk" >&2
  exit 1
fi

command -v yt-dlp >/dev/null || echo "warning: yt-dlp not found (brew install yt-dlp)"
command -v ffmpeg >/dev/null || echo "warning: ffmpeg not found (brew install ffmpeg)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$REPO/app/AppIcon.icns" "$APP/Contents/Resources/"

cat > "$APP/Contents/MacOS/VideoGrabber" <<SH
#!/bin/bash
exec "$PY" "$REPO/app/videograbber.py" >>"\$HOME/Library/Logs/VideoGrabber.log" 2>&1
SH
chmod +x "$APP/Contents/MacOS/VideoGrabber"

cat > "$APP/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>VideoGrabber</string>
  <key>CFBundleDisplayName</key><string>VideoGrabber</string>
  <key>CFBundleIdentifier</key><string>local.videograbber</string>
  <key>CFBundleExecutable</key><string>VideoGrabber</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSMultipleInstancesProhibited</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PL

echo "Installed $APP (python: $PY)"
echo "Next: chrome://extensions → Developer mode → Load unpacked → $REPO/extension"
