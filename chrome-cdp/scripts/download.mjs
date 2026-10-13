// Trigger a download with JavaScript in the selected tab, wait for the file
// and move it to the output folder.
//
// Chrome lets a tab download only once without a user gesture; later
// downloads wait on a prompt in the address bar that a screenshot does not
// show, so a script sees no file and no error. A DevTools download behavior
// skips that check and saves into our own folder while this browser-level
// connection stays open.
//
// Usage: node download.mjs -e "<js body that starts a download>" [--out dir] [--timeout seconds]
//        node download.mjs <file.js> [--out dir] [--timeout seconds]

import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, extname, join } from 'node:path'
import { connect } from './cdp.mjs'

const args = process.argv.slice(2)
const opt = (name, fallback) => (i => (i >= 0 ? args.splice(i, 2)[1] : fallback))(args.indexOf(name))
const outDir = opt('--out', join(tmpdir(), 'chrome-cdp', 'downloads'))
const timeout = Number(opt('--timeout', 120)) * 1000
const inline = opt('-e')
const body = inline ?? (args[0] && readFileSync(args[0], 'utf8'))
if (!body) {
    console.error('usage: node download.mjs -e "<js>" | <file.js> [--out dir] [--timeout seconds]')
    process.exit(1)
}

const downloadDir = mkdtempSync(join(tmpdir(), 'chrome-cdp-download-'))
const browser = await connect({ target: 'browser' })
await browser.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloadDir })

const cdp = await connect()
try {
    await cdp.run(body)
}
finally {
    cdp.close()
}

// Wait for a new file whose size has settled.
const deadline = Date.now() + timeout
let file = null
let lastSize = -1
while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 1000))
    const added = readdirSync(downloadDir).filter(name => !name.endsWith('.crdownload') && !name.startsWith('.'))
    if (added.length === 0) continue
    const candidate = join(downloadDir, added[0])
    const size = statSync(candidate).size
    if (candidate === file && size === lastSize) break
    file = candidate
    lastSize = size
}
browser.close()
if (!file) {
    rmSync(downloadDir, { recursive: true, force: true })
    console.error(`no download within ${timeout / 1000}s; check the page console`)
    process.exit(1)
}

mkdirSync(outDir, { recursive: true })
let target = join(outDir, basename(file))
for (let n = 1; existsSync(target); n++) {
    target = join(outDir, `${basename(file, extname(file))}-${n}${extname(file)}`)
}
renameSync(file, target)
rmSync(downloadDir, { recursive: true, force: true })
console.log(`saved ${target} (${(lastSize / 1024).toFixed(1)}KB)`)
