// Screenshot the selected tab: the viewport, or the whole page with --full.
// The image is at the device pixel ratio (2x on Retina), so divide image
// coordinates by it before using them as click coordinates.
//
// Usage: node shot.mjs [out.png] [--full]

import { writeFileSync } from 'node:fs'
import { connect } from './cdp.mjs'

const args = process.argv.slice(2)
const full = args.includes('--full')
const out = args.find(a => !a.startsWith('--')) || 'shot.png'

const cdp = await connect()
try {
    const params = { format: 'png' }
    if (full) {
        const { result } = await cdp.send('Page.getLayoutMetrics')
        const { width, height } = result.cssContentSize
        Object.assign(params, { captureBeyondViewport: true, clip: { x: 0, y: 0, width, height, scale: 1 } })
    }
    const { result, error } = await cdp.send('Page.captureScreenshot', params)
    if (error) throw new Error(error.message)
    writeFileSync(out, Buffer.from(result.data, 'base64'))
    console.log(out)
}
catch (error) {
    console.error(String(error))
    process.exitCode = 1
}
finally {
    cdp.close()
}
