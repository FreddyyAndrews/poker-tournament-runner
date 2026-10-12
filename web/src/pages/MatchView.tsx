import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type SeatView } from '../api'
import { ActionBar } from '../components/ActionBar'
import { Controls } from '../components/Controls'
import { HandLog } from '../components/HandLog'
import { Inspector } from '../components/Inspector'
import { PokerTable, playerColor } from '../components/PokerTable'
import { StackChart } from '../components/StackChart'
import { Standings } from '../components/Standings'
import { useMatch } from '../replay/useMatch'
import { forgetSeat, rememberSeat, rememberedSeat, seatHash } from '../seatMemory'
import { usePlayback } from '../replay/usePlayback'

type Tab = 'hand' | 'decision' | 'chart'

export function MatchView({ matchKey, seatToken, playAs }: { matchKey: string; seatToken?: string; playAs?: string }) {
  const data = useMatch(matchKey, playAs)
  const { timeline, length, status } = data
  const running = status === 'running'
  const events = useCallback(() => timeline.list, [timeline])
  const pb = usePlayback(events, length, running, running)
  const [tab, setTab] = useState<Tab>('hand')
  const [showAll, setShowAll] = useState(!playAs)
  const playing = !!seatToken
  const [openSeats, setOpenSeats] = useState<{ name: string; token: string }[]>([])

  // Holding a seat: remember it. Not holding one: if this browser remembers a
  // seat in this (live) match, take it back; otherwise offer any open human seat.
  useEffect(() => {
    if (seatToken && playAs) { rememberSeat(matchKey, seatToken, playAs); return }
    if (status !== 'running') return
    let alive = true
    const saved = rememberedSeat(matchKey)
    ;(async () => {
      if (saved) {
        try {
          const sv = await api.seat(saved.token)
          if (alive && !sv.finished) { location.replace(seatHash(matchKey, saved.token, saved.name)); return }
        } catch { forgetSeat(matchKey) }
      }
      try {
        const seats = await api.openSeats(matchKey)
        if (alive) setOpenSeats(seats.map(x => ({ name: x.name, token: x.token })))
      } catch { /* offer nothing */ }
    })()
    return () => { alive = false }
  }, [matchKey, seatToken, playAs, status])
  const [reviewing, setReviewing] = useState(false)   // playing, but looking back at earlier hands
  const [seat, setSeat] = useState<SeatView | null>(null)
  const [turn, setTurn] = useState<{ bot: string; kind: string; time_left_ms: number }[]>([])

  const state = useMemo(() => timeline.stateAt(pb.cursor), [timeline, pb.cursor, length])
  // Chips fly between the previous and current state when the table moves forward
  // one event at a watchable speed; never when scrubbing or at high speed.
  const lastCursor = useRef(pb.cursor)
  const stepped = pb.cursor === lastCursor.current + 1
  useEffect(() => { lastCursor.current = pb.cursor }, [pb.cursor])
  const animMs = stepped && pb.speed <= 4 ? Math.max(140, Math.round(420 / pb.speed)) : 0
  const prevState = useMemo(() => (animMs ? timeline.stateAt(pb.cursor - 1) : null),
                            [timeline, pb.cursor, animMs])
  const handStarts = useMemo(() => {
    const out: number[] = []
    timeline.list.forEach((e, i) => { if (e.type === 'hand_start') out.push(i) })
    return out
  }, [timeline, length])
  const handFrom = useMemo(() => {
    let from = 0
    for (const i of handStarts) { if (i <= pb.cursor) from = i; else break }
    return from
  }, [handStarts, pb.cursor])

  const start = timeline.list[0]?.type === 'match_start' ? timeline.list[0] : null
  const remoteSeats = start ? start.seats.filter(s => s.kind === 'human' || s.kind === 'mcp') : []

  // Holding a seat: poll it for our pending decision. Spectating a match with
  // human/LLM seats: poll who the table is waiting on.
  useEffect(() => {
    if (!running || (!seatToken && remoteSeats.length === 0)) return
    let alive = true
    const tick = async () => {
      try {
        if (seatToken) { const sv = await api.seat(seatToken); if (alive) setSeat(sv) }
        const t = await api.turn(matchKey); if (alive) setTurn(t)
      } catch { /* the next poll will retry */ }
    }
    tick()
    const t = setInterval(tick, 300)
    return () => { alive = false; clearInterval(t) }
  }, [running, seatToken, matchKey, remoteSeats.length])
  useEffect(() => { if (!running && seatToken) api.seat(seatToken).then(setSeat).catch(() => {}) }, [running, seatToken])
  const names = useMemo(() => start ? start.seats.map(s => s.name) : [], [start])
  // Seat order fixes colours; players who join later (multi-table) get a stable colour from their name.
  const colorOf = useCallback((n: string) => {
    const i = names.indexOf(n)
    if (i >= 0) return playerColor(i)
    let h = 0
    for (const c of n) h = (h * 31 + c.charCodeAt(0)) >>> 0
    return playerColor(h % 8)
  }, [names])
  const totalHands = data.meta?.summary?.hands ?? handStarts.length

  // Playing: the table stays live unless you choose to review, and your turn
  // always brings you back.
  // A turn that *starts* while you review brings you back; opening Review during
  // your turn is allowed (the banner then shows your clock).
  const myTurnNow = playing && !!seat?.pending
  const pendingId = seat?.pending?.id ?? null
  const lastPending = useRef<number | null>(null)
  useEffect(() => {
    if (!playing || !running) return
    if (pendingId !== null && pendingId !== lastPending.current && reviewing) setReviewing(false)
    lastPending.current = pendingId
    if (!reviewing && !pb.live) pb.goLive()
  }, [playing, running, pendingId, reviewing, pb])

  // Keyboard: space play/pause, arrows step, shift+arrows hands, L live.
  // While playing live these keys belong to the action dock instead.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT') return
      if (playing && running && !reviewing) return
      if (e.key === ' ') { e.preventDefault(); pb.toggle() }
      else if (e.key === 'ArrowRight' && !e.shiftKey) pb.step(1)
      else if (e.key === 'ArrowLeft' && !e.shiftKey) pb.step(-1)
      else if (e.key === 'ArrowRight' && e.shiftKey) pb.seek(handStarts.find(i => i > pb.cursor) ?? length - 1)
      else if (e.key === 'ArrowLeft' && e.shiftKey) pb.seek([...handStarts].reverse().find(i => i < pb.cursor) ?? 0)
      else if (e.key.toLowerCase() === 'l' && running) pb.goLive()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pb, handStarts, length, running, playing, reviewing])

  if (data.error) return <div className="container"><h1>Match not found</h1><p className="muted">{data.error}</p></div>

  const cfg = start?.config
  const jobId = matchKey.split('/')[0]
  const last = matchKey.split('/').pop()!
  const title = /^t\d+$/.test(last) && matchKey.includes('/tables/') ? `Table ${last.slice(1)}` : last
  return (
    <div className="match">
      <div className="stage">
        <div className="stage-head">
          {matchKey.includes('/') && (
            <a className="sub" href={`#/run/${matchKey.split('/matches/')[0]}`}>{matchKey.split('/matches/')[0]} /</a>
          )}
          <span className="title" data-testid="match-title">{title}</span>
          {running ? <span className="pill live" data-testid="status"><span className="dot" />Live</span>
            : status === 'complete' ? <span className="pill" data-testid="status">Complete</span>
            : status === 'incomplete' ? <span className="pill" data-testid="status">Didn't finish</span>
            : status === 'cancelled' ? <span className="pill" data-testid="status">Stopped</span>
            : status === 'error' ? <span className="pill" data-testid="status">Error</span> : null}
          {cfg && (
            <span className="facts num">
              <span>{cfg.mode === 'freezeout' ? 'Freezeout' : cfg.mode === 'cash' ? `Cash, ${cfg.hands} hands` : `${cfg.hands} hands`}</span>
              <span>Stack {cfg.starting_stack.toLocaleString('en-US')}</span>
              <span>{cfg.structure.name}</span>
              <span className="muted">seed {cfg.seed}</span>
            </span>
          )}
          <span className="spacer" />
          <button className="ghost" onClick={() => setShowAll(v => !v)} data-testid="toggle-cards"
                  title="Spectator view shows every hole card; fair view hides them until showdown">
            {showAll ? 'Hide hole cards' : 'Show hole cards'}
          </button>
          {running && <button className="ghost" onClick={() => api.cancel(jobId)}>
            {matchKey.includes('/') ? 'Stop run' : 'Stop'}</button>}
          {status === 'complete' && <a href={api.exportUrl(matchKey)}><button className="ghost">Download</button></a>}
        </div>

        <div className="table-wrap">
          <PokerTable state={state} showAll={showAll} colorOf={colorOf} me={playAs}
                      prev={prevState} animMs={animMs}
                      waitingFor={running && pb.live && turn[0] ? turn[0].bot : undefined} />
        </div>

        {running && turn.length > 0 && !(seatToken && turn.some(t => t.bot === playAs)) && !playing && (
          <div className="notice num" data-testid="waiting">
            Waiting for <b>{turn[0].bot}</b> ({turn[0].kind === 'mcp' ? 'LLM over MCP' : 'human'}),
            {' '}{Math.ceil(turn[0].time_left_ms / 1000)}s left
          </div>
        )}
        {running && remoteSeats.some(s => s.kind === 'mcp') && (
          <div className="notice" data-testid="mcp-notice">
            {remoteSeats.filter(s => s.kind === 'mcp').map(s => s.name).join(', ')} {remoteSeats.filter(s => s.kind === 'mcp').length > 1 ? 'are' : 'is'} played
            by an LLM over MCP. Connect one with <code>claude mcp add --transport http poker {location.origin}/mcp</code>,
            then ask it to call <code>list_open_seats</code> and play.
          </div>
        )}
        {!seatToken && running && openSeats.length > 0 && (
          <div className="notice rejoin" data-testid="rejoin">
            <span>This match has an open seat for a human player.</span>
            <span className="spacer" />
            {openSeats.map(o => (
              <button key={o.token} className="primary small" data-testid={`play-as-${o.name}`}
                      onClick={() => { rememberSeat(matchKey, o.token, o.name); location.hash = seatHash(matchKey, o.token, o.name) }}>
                Play as {o.name}</button>
            ))}
          </div>
        )}
        {seatToken && !reviewing && (
          <ActionBar token={seatToken} seat={seat}
                     result={(() => {
                       const r = data.meta?.summary?.results?.find(x => x.bot === playAs)
                       return r ? { place: r.place, delta: r.delta } : null
                     })()} />
        )}

        {playing && running && !reviewing ? (
          <div className="play-bar num" data-testid="play-bar">
            <span className="pill live"><span className="dot" />Live</span>
            <span className="sub">Hand {state.hand !== null ? state.hand + 1 : '-'}</span>
            {turn[0] && turn[0].bot !== playAs && <span className="muted">waiting for {turn[0].bot}</span>}
            <span className="spacer" />
            <button className="ghost" onClick={() => { setReviewing(true); pb.pause() }} data-testid="review">Review hands</button>
          </div>
        ) : (
          <>
            {playing && running && reviewing && (
              <div className="review-banner" data-testid="review-banner">
                {myTurnNow
                  ? <span><b>It's your turn</b> ({Math.ceil((seat?.pending?.time_left_ms ?? 0) / 1000)}s left).</span>
                  : <span>Reviewing earlier hands. The game is still going; your next turn brings you back.</span>}
                <span className="spacer" />
                <button className="primary small" onClick={() => setReviewing(false)} data-testid="back-to-table">
                  Back to the table</button>
              </div>
            )}
            <Controls pb={pb} length={length} running={running && !playing} handStarts={handStarts}
                      currentHand={state.hand} totalHands={totalHands} />
          </>
        )}
      </div>

      <aside className="side">
        <div className="panel" style={{ flex: 'none', paddingBottom: 6 }}>
          <h2>Standings</h2>
          <Standings state={state} colorOf={colorOf} />
        </div>
        <div className="tabs" role="tablist">
          {(['hand', 'decision', 'chart'] as Tab[]).map(t => (
            <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)} role="tab"
                    data-testid={`tab-${t}`}>
              {t === 'hand' ? 'Hand log' : t === 'decision' ? 'Decision' : cfg?.mode === 'cash' ? 'Net' : 'Stacks'}
            </button>
          ))}
        </div>
        <div className="panel">
          {tab === 'hand' && <HandLog events={timeline.list} from={handFrom} cursor={pb.cursor}
                                      onPick={i => { pb.seek(i); if (timeline.list[i].type === 'decision') setTab('decision') }} />}
          {tab === 'decision' && <Inspector d={state.lastDecision} />}
          {tab === 'chart' && <StackChart events={timeline.list} cursor={pb.cursor} names={names}
                                          start={cfg?.starting_stack ?? 0} cash={cfg?.mode === 'cash'} colorOf={colorOf}
                                          onSeekHand={h => pb.seek(handStarts[h] ?? 0)} />}
        </div>
      </aside>
    </div>
  )
}
