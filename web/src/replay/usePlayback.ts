import { useCallback, useEffect, useRef, useState } from 'react'
import type { PokerEvent } from '../types'

// Display time per event at 1x, in ms (mirrors the server's pacing).
const DELAY: Record<string, number> = {
  match_start: 300, level: 400, hand_start: 600, post: 150, decision: 600, deal: 750,
  showdown: 1200, award: 600, hand_end: 900, elimination: 1200, seat_change: 300,
  table_closed: 600, match_end: 0,
}

export const SPEEDS = [0.5, 1, 2, 4, 8, 16, 32]

export type Playback = {
  cursor: number            // index of the last applied event (-1 = before start)
  playing: boolean
  speed: number
  live: boolean             // following the newest event as it arrives
  setSpeed: (s: number) => void
  play: () => void
  pause: () => void
  toggle: () => void
  seek: (i: number) => void
  step: (n: number) => void
  goLive: () => void
}

// Cursor over a growing event list. In live mode the cursor sticks to the
// newest event (the server already paces live matches). Scrubbing back leaves
// live mode; playing forward and catching up re-enters it if still running.
export function usePlayback(events: () => PokerEvent[], length: number, running: boolean,
                            startLive: boolean): Playback {
  const [cursor, setCursor] = useState(-1)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [live, setLive] = useState(startLive)
  const started = useRef(false)

  // Initial position once events arrive: live edge, or the first hand.
  useEffect(() => {
    if (started.current || length === 0) return
    started.current = true
    if (startLive) {
      setLive(true)
      setCursor(length - 1)
    } else {
      const firstHand = events().findIndex(e => e.type === 'hand_start')
      setCursor(firstHand >= 0 ? firstHand : 0)
    }
  }, [length, startLive, events])

  useEffect(() => {
    if (live) setCursor(length - 1)
  }, [live, length])

  useEffect(() => {
    if (!playing || live) return
    if (cursor >= length - 1) {
      setPlaying(false)
      if (running) setLive(true)
      return
    }
    const next = events()[cursor + 1]
    const t = setTimeout(() => setCursor(c => Math.min(c + 1, length - 1)),
                         (DELAY[next?.type] ?? 300) / speed)
    return () => clearTimeout(t)
  }, [playing, live, cursor, length, speed, running, events])

  const seek = useCallback((i: number) => {
    setLive(false)
    setCursor(Math.max(-1, Math.min(i, length - 1)))
  }, [length])

  return {
    cursor, playing: playing || (live && running), speed, live, setSpeed,
    play: () => { if (cursor >= length - 1 && !running) setCursor(-1); setLive(false); setPlaying(true) },
    pause: () => { setLive(false); setPlaying(false) },
    toggle: () => {
      if (playing || (live && running)) { setLive(false); setPlaying(false) }
      else { if (cursor >= length - 1 && !running) setCursor(-1); setPlaying(true) }
    },
    seek,
    step: (n: number) => { setPlaying(false); seek(cursor + n) },
    goLive: () => { setPlaying(false); setLive(true) },
  }
}
