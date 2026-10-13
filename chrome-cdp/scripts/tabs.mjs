// List the open tabs and mark the one the other scripts will drive
// (the first match of TAB_MATCH).
//
// Usage: node tabs.mjs

import { listPages, selectedPage } from './cdp.mjs'

const selected = await selectedPage()
for (const p of await listPages()) {
    console.log(`${p.id === selected?.id ? '*' : ' '} ${p.title.slice(0, 60).padEnd(60)} ${p.url}`)
}
if (!selected) {
    console.error('no tab matches TAB_MATCH')
    process.exitCode = 2
}
