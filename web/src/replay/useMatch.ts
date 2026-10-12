import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { MatchMeta, MatchStatus, PokerEvent } from '../types'
import { Timeline } from './state'

export type MatchData = {
  meta: MatchMeta | null
  timeline: Timeline
  length: number          // events received so far
  status: MatchStatus | 'loading'
  error: string | null
}

// Load a match's events, then (if it is still being played) follow the live
// stream from where the load stopped. New events are appended to one Timeline.
export function useMatch(key: string, as?: string): MatchData {
  const timeline = useRef(new Timeline())
  const [length, setLength] = useState(0)
  const [meta, setMeta] = useState<MatchMeta | null>(null)
  const [status, setStatus] = useState<MatchStatus | 'loading'>('loading')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    timeline.current = new Timeline()
    setLength(0); setMeta(null); setStatus('loading'); setError(null)
    let source: EventSource | null = null
    let cancelled = false

    const refreshMeta = () => api.match(key).then(m => { if (!cancelled) { setMeta(m); setStatus(m.status) } })

    ;(async () => {
      try {
        const m = await api.match(key)
        if (cancelled) return
        setMeta(m)
        const first = await api.events(key, 0, as)
        if (cancelled) return
        timeline.current.append(first.events)
        setLength(timeline.current.length)
        setStatus(first.status as MatchStatus)
        if (first.status !== 'running') return

        source = new EventSource(api.streamUrl(key, first.next, as))
        source.addEventListener('poker', (msg) => {
          timeline.current.append([JSON.parse((msg as MessageEvent).data) as PokerEvent])
          setLength(timeline.current.length)
        })
        source.addEventListener('end', () => { source?.close(); refreshMeta() })
        source.onerror = () => { source?.close(); refreshMeta() }
      } catch (e) {
        if (!cancelled) setError(String(e))
      }
    })()

    return () => { cancelled = true; source?.close() }
  }, [key, as])

  return { meta, timeline: timeline.current, length, status, error }
}
