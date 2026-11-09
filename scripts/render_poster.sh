#!/usr/bin/env bash
# Render docs/poster/poster.html (A0 portrait) to docs/poster/poster.pdf and a PNG preview with a headless
# Chrome, Chromium or Edge (Linux, macOS, or a Windows browser from WSL). Set CHROME=/path/to/browser to choose one.
# Needs network for the Google Fonts (falls back to serif/sans). Poster charts: tools/figures.py --palette poster.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/docs/poster"
CHROME="${CHROME:-}"
for c in chromium chromium-browser google-chrome google-chrome-stable \
         "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" "/Applications/Chromium.app/Contents/MacOS/Chromium" \
         "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe" \
         "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"; do
  [[ -z "$CHROME" ]] && { command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]]; } && CHROME="$c"
done
[[ -n "$CHROME" ]] || { echo "no Chrome/Chromium/Edge found (set CHROME=...)"; exit 1; }

if [[ "$CHROME" == /mnt/c/* ]]; then
  # Windows browser: render from a Windows temp folder (it can't read the WSL filesystem reliably)
  WTMP="$(wslpath "$(cmd.exe /c echo %TEMP% 2>/dev/null | tr -d '\r')")/swarm-poster"
  rm -rf "$WTMP" && mkdir -p "$WTMP" && cp "$SRC"/poster.html "$SRC"/*.svg "$SRC"/*.png "$WTMP"/
  IN="file:///$(wslpath -w "$WTMP/poster.html" | tr '\\' '/')"
  OUT_PDF="$(wslpath -w "$WTMP/poster.pdf")"; OUT_PNG="$(wslpath -w "$WTMP/preview.png")"
else
  WTMP="$SRC"; IN="file://$SRC/poster.html"; OUT_PDF="$SRC/poster.pdf"; OUT_PNG="$SRC/preview.png"
fi
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --virtual-time-budget=8000 \
  --print-to-pdf="$OUT_PDF" "$IN" >/dev/null 2>&1
# preview: A0 at 1/4 scale (841 x 1189 mm ~ 3179 x 4494 px at 96 dpi)
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=8000 --force-device-scale-factor=0.35 \
  --window-size=3179,4494 --screenshot="$OUT_PNG" "$IN" >/dev/null 2>&1
if [[ "$WTMP" != "$SRC" ]]; then cp "$WTMP/poster.pdf" "$WTMP/preview.png" "$SRC"/; fi
echo "poster: $SRC/poster.pdf  (preview: $SRC/preview.png)"
