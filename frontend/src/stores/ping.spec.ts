import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { PingEvent } from '@/api/types'
import { signInAfterSocketClose } from '@/api/http'
import { usePingStore } from './ping'

// The owner of "the session has ended" answers a 4401 close with its sign-in navigation
// (a promise) and any other close with null; the spec reads what the store asked it.
vi.mock('@/api/http', () => ({
  signInAfterSocketClose: vi.fn((code: number) => (code === 4401 ? Promise.resolve() : null)),
}))

class MockWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  static CLOSED = 3
  static instances: MockWebSocket[] = []
  url: string
  readyState = MockWebSocket.CONNECTING
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }
  fireOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }
  fireRaw(data: string) {
    this.onmessage?.({ data } as MessageEvent)
  }
  fireClose(code = 1006) {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close', { code }))
  }
  close() {
    this.fireClose()
  }
}

const realWebSocket = globalThis.WebSocket

/** The i-th socket the store dialled; throws (failing the test) if it never did. */
function socket(i: number): MockWebSocket {
  const ws = MockWebSocket.instances[i]
  if (!ws) throw new Error(`no WebSocket #${i} was opened`)
  return ws
}

function ping(id: string): PingEvent {
  return { type: 'ping', id, title: `t-${id}`, sent_at: 1 }
}

describe('ping store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    MockWebSocket.instances = []
    ;(globalThis as any).WebSocket = MockWebSocket
    vi.useFakeTimers()
    vi.mocked(signInAfterSocketClose).mockClear()
  })
  afterEach(() => {
    vi.useRealTimers()
    ;(globalThis as any).WebSocket = realWebSocket
  })

  // Mutation killed: dropping encodeURIComponent, or the token query.
  it('dials /ws/ping with the URL-encoded token (none when null)', () => {
    const s = usePingStore()
    s.connect('a b&c')
    expect(socket(0).url).toBe(`ws://${location.host}/ws/ping?token=a%20b%26c`)
    s.disconnect()
    s.connect(null)
    expect(socket(1).url).toBe(`ws://${location.host}/ws/ping`)
  })

  // Mutation killed: connect not guarding on an already-open/connecting socket.
  it('does not dial a second socket while connecting or open', () => {
    const s = usePingStore()
    s.connect('t')
    s.connect('t')
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(s.connecting).toBe(true)
    socket(0).fireOpen()
    s.connect('t')
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(s.connected).toBe(true)
    expect(s.connecting).toBe(false)
  })

  // Mutation killed: _handleEvent appending in place / ignoring order; drain not clearing.
  it('queues pings in order and drain returns then clears them', () => {
    const s = usePingStore()
    s.connect('t')
    const ws = socket(0)
    ws.fireRaw(JSON.stringify(ping('1')))
    ws.fireRaw(JSON.stringify(ping('2')))
    expect(s.pendingPings.map((p) => p.id)).toEqual(['1', '2'])
    expect(s.drainPings().map((p) => p.id)).toEqual(['1', '2'])
    expect(s.pendingPings).toEqual([])
    expect(s.drainPings()).toEqual([])
  })

  // Mutation killed: queueing events whose type is not "ping".
  it('ignores non-ping event types', () => {
    const s = usePingStore()
    s._injectEventForTest({ type: 'other', id: 'x' } as unknown as PingEvent)
    expect(s.pendingPings).toEqual([])
  })

  // Mutation killed: swallowing the parse error without recording it.
  it('records a parse failure in lastError and queues nothing', () => {
    const s = usePingStore()
    s.connect('t')
    socket(0).fireRaw('{not json')
    expect(s.lastError).toMatch(/^bad ws payload: /)
    expect(s.pendingPings).toEqual([])
  })

  // Mutation killed: backoff not doubling, or not resetting on open.
  it('reconnects with 1s, 2s, 4s backoff and resets after a successful open', () => {
    const s = usePingStore()
    s.connect('tok')
    socket(0).fireClose()
    expect(s.connected).toBe(false)
    vi.advanceTimersByTime(999)
    expect(MockWebSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(MockWebSocket.instances).toHaveLength(2)
    socket(1).fireClose()
    vi.advanceTimersByTime(1999)
    expect(MockWebSocket.instances).toHaveLength(2)
    vi.advanceTimersByTime(1)
    expect(MockWebSocket.instances).toHaveLength(3)
    socket(2).fireOpen()
    socket(2).fireClose()
    vi.advanceTimersByTime(1000)
    expect(MockWebSocket.instances).toHaveLength(4)
    expect(socket(3).url).toContain('token=tok')
  })

  it.each([
    ['while open', true],
    ['refused at connect', false],
  ])('a 4401 close %s goes to the sign-in page and never reconnects', (_when, opened) => {
    const s = usePingStore()
    s.connect('tok')
    if (opened) socket(0).fireOpen()
    socket(0).fireClose(4401)
    vi.advanceTimersByTime(60_000)
    expect(vi.mocked(signInAfterSocketClose).mock.calls).toEqual([[4401]])
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(s.connected).toBe(false)
  })

  // Mutation killed: disconnect not cancelling the timer / not flagging intent.
  it('disconnect cancels a pending reconnect and does not reschedule', () => {
    const s = usePingStore()
    s.connect('t')
    socket(0).fireClose()
    s.disconnect()
    vi.advanceTimersByTime(60_000)
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(s.connected).toBe(false)
    expect(s.connecting).toBe(false)
  })

  it('disconnect of a live socket closes it without scheduling a reconnect', () => {
    const s = usePingStore()
    s.connect('t')
    socket(0).fireOpen()
    s.disconnect()
    expect(socket(0).readyState).toBe(MockWebSocket.CLOSED)
    vi.advanceTimersByTime(60_000)
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(s.connected).toBe(false)
  })

  // Mutation killed: onerror not recording the message.
  it('onerror sets lastError; a new connect clears it', () => {
    const s = usePingStore()
    s.connect('t')
    socket(0).onerror?.(new Event('error'))
    expect(s.lastError).toBe('websocket error')
    s.disconnect()
    s.connect('t')
    expect(s.lastError).toBeNull()
  })
})
