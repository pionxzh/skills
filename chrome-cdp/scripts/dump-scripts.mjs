// Save every script the tab has parsed (lazy chunks, inline, blob) whose
// source matches a regex. Files saved from the network or the Sources panel
// miss chunks loaded later, so ask the page's debugger instead. Third-party
// code: keep the dump out of git.
//
// Usage: node dump-scripts.mjs <regex> [out-dir]

import { mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { connect } from './cdp.mjs'

const [pattern, outArg] = process.argv.slice(2)
if (!pattern) throw new Error('usage: node dump-scripts.mjs <regex> [out-dir]')
const re = new RegExp(pattern)
const out = outArg || join(tmpdir(), 'chrome-cdp', 'scripts')

const cdp = await connect()
try {
    const scripts = []
    cdp.on('Debugger.scriptParsed', p => scripts.push(p))
    await cdp.send('Debugger.enable')
    await new Promise(r => setTimeout(r, 2000)) // scriptParsed replays all parsed scripts
    mkdirSync(out, { recursive: true })
    let hits = 0
    for (const s of scripts) {
        const src = (await cdp.send('Debugger.getScriptSource', { scriptId: s.scriptId })).result?.scriptSource || ''
        if (!re.test(src)) continue
        hits++
        const file = `${out}/page-${s.scriptId}.js`
        writeFileSync(file, src)
        console.log(`${s.url.split('?')[0].slice(0, 120) || '(inline)'} ${src.length} → ${file}`)
    }
    console.log(`${scripts.length} scripts, ${hits} matched`)
    await cdp.send('Debugger.disable')
}
finally {
    cdp.close()
}
