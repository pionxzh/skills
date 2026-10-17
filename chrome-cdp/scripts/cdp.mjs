// Minimal CDP client for one tab of a Chrome started with
// --remote-debugging-port. Zero dependencies; needs Node 22+ for the global
// WebSocket.
//
// The tab is picked by TAB_MATCH (regex on URL). Default: the first page that
// is not DevTools or an extension popup; /json/list returns pages
// most-recently-used first.

export const CDP_PORT = Number(process.env.CDP_PORT || 9222)
const TAB_MATCH = process.env.TAB_MATCH ? new RegExp(process.env.TAB_MATCH) : /^(?!devtools:|chrome-extension:)/

export async function listPages() {
    const pages = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json()
    return pages.filter(p => p.type === 'page')
}

export async function selectedPage() {
    return (await listPages()).find(p => TAB_MATCH.test(p.url))
}

export async function connect({ target = 'page' } = {}) {
    const base = `http://127.0.0.1:${CDP_PORT}`
    let url
    if (target === 'browser') {
        url = (await (await fetch(`${base}/json/version`)).json()).webSocketDebuggerUrl
    }
    else {
        const page = await selectedPage()
        if (!page) throw new Error(`No tab matching ${TAB_MATCH} on :${CDP_PORT}. Run start-chrome.sh first.`)
        url = page.webSocketDebuggerUrl
    }

    const ws = new WebSocket(url)
    await new Promise((resolve, reject) => {
        ws.onopen = resolve
        ws.onerror = reject
    })

    let id = 0
    const pending = new Map()
    const listeners = new Map()
    ws.onmessage = (event) => {
        const message = JSON.parse(event.data)
        if (message.id) {
            pending.get(message.id)?.(message)
            pending.delete(message.id)
        }
        else if (message.method) {
            for (const cb of listeners.get(message.method) || []) cb(message.params)
        }
    }

    /** Send a CDP command. Resolves to the raw message: { result } or { error }. */
    const send = (method, params = {}) => new Promise((resolve) => {
        const messageId = ++id
        pending.set(messageId, resolve)
        ws.send(JSON.stringify({ id: messageId, method, params }))
    })

    /** Subscribe to a CDP event, e.g. on('Network.responseReceived', fn). */
    const on = (method, cb) => {
        if (!listeners.has(method)) listeners.set(method, [])
        listeners.get(method).push(cb)
    }

    /** Evaluate an expression in the page and return its value. Throws on page errors. */
    const evaluate = async (expression) => {
        const { result, error } = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
        if (error) throw new Error(error.message)
        if (result.exceptionDetails) {
            const details = result.exceptionDetails
            throw new Error(details.exception?.description || details.text)
        }
        return result.result.value
    }

    /** Run the body of an async function in the page. `return` gives the result. */
    const run = body => evaluate(`(async () => { ${body} })()`)

    // A dialog blocks the page, and only a session that had Page enabled when
    // it opened can answer it. So every session answers them: alerts and
    // beforeunload are accepted, confirm and prompt are dismissed unless
    // CDP_DIALOG=accept, so a script never confirms a destructive action by
    // accident.
    if (target === 'page') {
        on('Page.javascriptDialogOpening', ({ type, message }) => {
            const accept = type === 'alert' || type === 'beforeunload' || process.env.CDP_DIALOG === 'accept'
            console.error(`[dialog] ${type}: ${message} -> ${accept ? 'accept' : 'dismiss'}`)
            send('Page.handleJavaScriptDialog', { accept })
        })
        await send('Page.enable')
    }

    return { send, on, evaluate, run, close: () => ws.close() }
}
