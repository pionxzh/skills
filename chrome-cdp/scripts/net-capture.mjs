// Record the selected tab's API traffic to JSONL while someone (or a script)
// browses: XHR/fetch requests with bodies, plus WebSocket frames. Only the
// selected tab is recorded; a link that opens a new tab is not followed.
// Captures hold real session data: keep them out of git.
//
// Usage: node net-capture.mjs [--seconds N] [--out file.jsonl]
//        Stops on Ctrl-C if --seconds is not given.
// Env:   MAX_BODY (bytes kept per body / frame, default 262144)

import { createWriteStream, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { connect } from './cdp.mjs'

const args = process.argv.slice(2)
const opt = name => (i => (i >= 0 ? args[i + 1] : undefined))(args.indexOf(name))
const seconds = Number(opt('--seconds') || 0)
const stamp = new Date().toISOString().replace(/[:.]/g, '-')
const out = resolve(opt('--out') || join(tmpdir(), 'chrome-cdp', `capture-${stamp}.jsonl`))
const MAX_BODY = Number(process.env.MAX_BODY || 262144)

mkdirSync(dirname(out), { recursive: true })
const file = createWriteStream(out)
const write = rec => file.write(`${JSON.stringify({ t: Date.now(), ...rec })}\n`)
const clip = s => (s && s.length > MAX_BODY ? `${s.slice(0, MAX_BODY)}…[clipped ${s.length}]` : s)

const cdp = await connect()
const requests = new Map()
const counts = { http: 0, wsIn: 0, wsOut: 0 }

cdp.on('Network.requestWillBeSent', (p) => {
    requests.set(p.requestId, { method: p.request.method, url: p.request.url, postData: p.request.postData, hasPostData: p.request.hasPostData })
})
cdp.on('Network.responseReceived', (p) => {
    if (!['XHR', 'Fetch'].includes(p.type)) return
    const r = requests.get(p.requestId) || {}
    Object.assign(r, { type: p.type, status: p.response.status, mime: p.response.mimeType, keep: true })
    requests.set(p.requestId, r)
})
cdp.on('Network.loadingFinished', async (p) => {
    const r = requests.get(p.requestId)
    requests.delete(p.requestId)
    if (!r?.keep) return
    let body
    if (/json|text|javascript/.test(r.mime || '')) {
        const res = await cdp.send('Network.getResponseBody', { requestId: p.requestId })
        body = res.result ? (res.result.base64Encoded ? '[base64]' : res.result.body) : undefined
    }
    if (r.hasPostData && !r.postData) {
        const res = await cdp.send('Network.getRequestPostData', { requestId: p.requestId })
        r.postData = res.result?.postData
    }
    counts.http++
    console.log(`${r.status} ${r.method} ${r.url.slice(0, 140)} (${p.encodedDataLength}B)`)
    write({ kind: 'http', method: r.method, url: r.url, status: r.status, mime: r.mime, size: p.encodedDataLength, postData: clip(r.postData), body: clip(body) })
})
cdp.on('Network.webSocketCreated', (p) => {
    console.log(`WS open ${p.url}`)
    write({ kind: 'ws-open', id: p.requestId, url: p.url })
})
cdp.on('Network.webSocketFrameReceived', (p) => {
    counts.wsIn++
    write({ kind: 'ws-in', id: p.requestId, opcode: p.response.opcode, data: clip(p.response.payloadData) })
})
cdp.on('Network.webSocketFrameSent', (p) => {
    counts.wsOut++
    write({ kind: 'ws-out', id: p.requestId, opcode: p.response.opcode, data: clip(p.response.payloadData) })
})

await cdp.send('Network.enable', { maxPostDataSize: MAX_BODY })
const where = await cdp.evaluate('location.href')
console.log(`recording ${where} → ${out}`)
write({ kind: 'start', url: where })

const stop = () => {
    console.log(`stopped: ${counts.http} http, ${counts.wsIn} ws in, ${counts.wsOut} ws out → ${out}`)
    cdp.close()
    file.end(() => process.exit(0))
}
process.on('SIGINT', stop)
if (seconds) setTimeout(stop, seconds * 1000)
