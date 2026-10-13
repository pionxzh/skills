#!/bin/sh
# Start a dedicated Chrome with CDP, or reuse the one already on the port.
# Use one profile and one port per purpose: logins, cookies and extensions
# stay in the profile, so log in by hand once and scripts reuse the session.
#
# Env: CDP_PORT (9222), CHROME_PROFILE (~/.chrome-cdp), START_URL (about:blank),
#      CHROME_BIN (macOS Chrome, else google-chrome / chromium on PATH),
#      CHROME_ARGS (extra flags, word-split, e.g. "--headless=new")
PORT="${CDP_PORT:-9222}"
PROFILE="${CHROME_PROFILE:-$HOME/.chrome-cdp}"
START_URL="${START_URL:-about:blank}"
CHROME="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
if [ ! -x "$CHROME" ]; then
    CHROME="$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser)"
fi
if [ -z "$CHROME" ]; then
    echo "Chrome not found; set CHROME_BIN" >&2
    exit 1
fi

if curl -sf "http://127.0.0.1:$PORT/json/version" >/dev/null; then
    echo "already running on :$PORT"
    exit 0
fi

# shellcheck disable=SC2086 # CHROME_ARGS is split on purpose
nohup "$CHROME" --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check $CHROME_ARGS "$START_URL" >/dev/null 2>&1 &

for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -sf "http://127.0.0.1:$PORT/json/version" >/dev/null; then
        echo "started on :$PORT (profile $PROFILE)"
        exit 0
    fi
done
echo "failed to start Chrome on :$PORT" >&2
exit 1
