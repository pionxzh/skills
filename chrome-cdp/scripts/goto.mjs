// Navigate the selected tab and wait for the load event.
// Unlike `location.href = ...` through eval.mjs, this returns after the page
// has loaded, so the next script sees the new page.
//
// Usage: node goto.mjs <url> [--timeout seconds]

import { connect } from './cdp.mjs'

const args = process.argv.slice(2)
const url = args[0]
const timeout = Number((i => (i >= 0 ? args[i + 1] : 30))(args.indexOf('--timeout'))) * 1000
if (!url) throw new Error('usage: node goto.mjs <url> [--timeout seconds]')

const cdp = await connect()
try {
    const loaded = new Promise(resolve => cdp.on('Page.loadEventFired', resolve))
    await cdp.send('Page.enable')
    const { result, error } = await cdp.send('Page.navigate', { url })
    if (error || result.errorText) throw new Error(error?.message || result.errorText)
    const timer = new Promise((_, reject) => setTimeout(() => reject(new Error(`no load event within ${timeout / 1000}s`)), timeout))
    await Promise.race([loaded, timer])
    console.log(await cdp.evaluate('location.href'))
}
catch (error) {
    console.error(String(error))
    process.exitCode = 1
}
finally {
    cdp.close()
}
