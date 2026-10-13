---
name: chrome-cdp
description: Drive a real Chrome over the DevTools Protocol (CDP) with small zero-dependency Node scripts — start a dedicated Chrome with its own profile, navigate, run JavaScript in the page, trusted click / hover / typing, screenshots, downloads, network and WebSocket capture, and dumping the page's loaded scripts. Use when a task needs a logged-in or extension-equipped browser session on a live site, when headless automation gets blocked, or when you need to inspect what a web app actually sends and loads.
---

# Chrome over CDP

Scripts that attach to one Chrome started with `--remote-debugging-port`.
The browser keeps running between calls, so logins, cookies and extensions
survive and each script call is fast. All scripts need Node 22+ and nothing
else.

Paths below are relative to this skill's directory.

## When to use something else

- Rendering local HTML to an image, or any job that needs no login: a
  disposable headless browser (Puppeteer, Playwright) is simpler.
- Snapshots of the accessibility tree and ad-hoc clicks while exploring: the
  `chrome-devtools` MCP can attach to the same Chrome
  (`--browser-url=http://127.0.0.1:<port>`). Attach it rather than letting
  it launch its own browser, whose automation flags trip bot checks more
  often.

## Setup

```sh
CDP_PORT=9223 CHROME_PROFILE=~/.chrome-<purpose> START_URL=https://example.com scripts/start-chrome.sh
```

- One port and one profile per purpose. Two projects on the same port
  drive each other's tabs. Check what is listening first:
  `lsof -iTCP -sTCP:LISTEN -P | grep -i chrome`.
- Export `CDP_PORT` for every later script call, or pass it each time.
- The person logs in by hand the first time, and again when the session
  expires or a captcha / bot check appears. Install any extension the site
  needs (VPN, wallet) in the same profile. Do not script around captchas.
- `CHROME_ARGS="--headless=new"` runs without a window. Use it for local
  pages and tests only: headless Chrome has different fonts and is easier
  for sites to detect.
- The default binary is macOS Chrome; elsewhere it looks for
  `google-chrome` or `chromium`, or set `CHROME_BIN`.

## Scripts

| Script | What it does |
|---|---|
| `start-chrome.sh` | Start Chrome on `CDP_PORT` with `CHROME_PROFILE`, or reuse the one running |
| `tabs.mjs` | List tabs; `*` marks the one the other scripts drive |
| `goto.mjs <url>` | Navigate and wait for the load event |
| `eval.mjs -e "<body>"` / `eval.mjs file.js` | Run an async function body in the page; `return` gives the result |
| `click.mjs "<text>" [--selector css] [--hover]` | Trusted click or hover on the first visible element with that exact text, scrolled into view |
| `type.mjs <selector> [--text ...] [--clear] [--enter]` | Trusted typing; reads the text from stdin by default |
| `shot.mjs [out.png] [--full]` | Screenshot the viewport or the whole page |
| `download.mjs -e "<body>" [--out dir]` | Trigger a download in the page and collect the file |
| `net-capture.mjs [--seconds N] [--out f.jsonl]` | Record XHR / fetch with bodies and WebSocket frames to JSONL |
| `dump-scripts.mjs <regex> [out-dir]` | Save every parsed script (lazy chunks, inline) whose source matches |

`TAB_MATCH=<regex on URL>` picks the tab when several are open. Default:
the most recently used page that is not DevTools or an extension popup.
Run `tabs.mjs` first when unsure.

`cdp.mjs` is the client the others import: `connect()` returns
`{ send, on, evaluate, run, close }`. Write a new script on top of it when
a flow repeats, rather than growing a long `eval.mjs -e` string.

## Working rules

- **Trusted input vs `element.click()`.** `click.mjs` and `type.mjs` send
  CDP `Input.*` events, which count as user gestures. Use them for hover
  menus, popups, file pickers, framework-controlled inputs and anything
  that ignores synthetic events. A plain `el.click()` through `eval.mjs`
  is fine for ordinary buttons and links.
- **Secrets.** Pipe passwords into `type.mjs` from the keychain or a secret
  store (`security find-generic-password -s <name> -w | node scripts/type.mjs "input[type=password]"`).
  Never pass them with `--text`, `eval.mjs` or in a file.
- **Downloads.** Chrome blocks the second scripted download from a tab
  behind an address-bar prompt that screenshots do not show; the script
  sees no file and no error. `download.mjs` avoids it with
  `Browser.setDownloadBehavior`. After that call the check stays off and
  files go to `~/Downloads` until Chrome restarts.
- **Coordinates.** Screenshots are at the device pixel ratio (2x on
  Retina). Take click coordinates from `getBoundingClientRect()`, not from
  pixels in an image.
- **Navigation.** A full page load drops anything injected with
  `eval.mjs`. Use `goto.mjs` (waits for load) instead of
  `location.href = ...` when the next step depends on the new page.
- **Output stays out of git.** Captures, downloads and dumped scripts
  default to the system temp dir. They hold real session data or
  third-party code.
- **Be gentle with live sites.** Space out loops of requests, and stop at
  the first sign of rate limiting or a block page.

## Building a site-specific layer

For a site you automate repeatedly, copy `cdp.mjs` (and any script you
need) into that project, set its port, profile and start URL defaults, and
add the site's own scripts next to it: login, status check, the scrape or
test flow. A copy keeps the project runnable on its own; this skill is the
template it came from.
