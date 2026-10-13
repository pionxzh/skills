// Type into an element with trusted CDP input (Input.insertText), so
// framework-controlled inputs see real input events. The text comes from
// stdin by default, so secrets stay out of argv and shell history; it is
// never printed.
//
// Usage: security find-generic-password -s my-site -w | node type.mjs "input[type=password]"
//        node type.mjs "#search" --text "query" [--clear] [--enter]

import { readFileSync } from 'node:fs'
import { connect } from './cdp.mjs'

const args = process.argv.slice(2)
const opt = name => (i => (i >= 0 ? args.splice(i, 2)[1] : undefined))(args.indexOf(name))
const literal = opt('--text')
const clear = args.includes('--clear')
const enter = args.includes('--enter')
const selector = args.find(a => !a.startsWith('--'))
if (!selector) throw new Error('usage: node type.mjs <selector> [--text "..."] [--clear] [--enter]   (text from stdin otherwise)')
const text = literal ?? readFileSync(0, 'utf8').replace(/\r?\n$/, '')

const cdp = await connect()
try {
    const found = await cdp.run(`
        const el = document.querySelector(${JSON.stringify(selector)})
        if (!el) return false
        el.scrollIntoView({ block: 'center' })
        el.focus()
        if (${clear}) el.select?.()
        return true
    `)
    if (!found) throw new Error(`no element matching ${selector}`)
    await cdp.send('Input.insertText', { text })
    if (enter) {
        const key = { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 }
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', text: '\r', ...key })
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...key })
    }
    console.log(`typed ${text.length} chars into ${selector}${enter ? ' + Enter' : ''}`)
}
catch (error) {
    console.error(String(error))
    process.exitCode = 1
}
finally {
    cdp.close()
}
