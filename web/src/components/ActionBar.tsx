import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type SeatView } from '../api'
import { actionLabel } from '../replay/state'

const fmt = (n: number) => n.toLocaleString('en-US')

type Props = {
  token: string
  seat: SeatView | null
  result?: { place: number | null; delta: number } | null   // your result when the match is over
}

// The decision dock shown when you hold a seat: on your turn, the key numbers,
// three big actions (fold / check-call / raise), raise sizing, a countdown and
// keyboard shortcuts; otherwise what you last did and what the table is doing.
export function ActionBar({ token, seat, result }: Props) {
  const p = seat?.pending
  const s = p?.state
  const maxTo = s ? s.your_stack + s.your_bet_this_street : 0
  const minTo = s ? Math.min(s.min_raise_to, maxTo) : 0
  const canRaise = !!s && s.your_stack > s.amount_owed && maxTo > s.current_bet
  const toCall = s ? Math.min(s.amount_owed, s.your_stack) : 0

  const [raiseTo, setRaiseTo] = useState(0)
  const [sentId, setSentId] = useState<number | null>(null)
  const [last, setLast] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())
  const [seenAt, setSeenAt] = useState(Date.now())
  const raiseInput = useRef<HTMLInputElement>(null)

  useEffect(() => { if (p) { setRaiseTo(minTo); setError(null) } }, [p?.id])   // a new decision
  useEffect(() => { setSeenAt(Date.now()) }, [p?.time_left_ms])
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 200); return () => clearInterval(t) }, [])

  const sizes = useMemo(() => {
    if (!s || !canRaise) return []
    const potAfterCall = s.pot + s.amount_owed
    const unit = Math.max(1, s.blinds.sb)                       // tidy amounts: multiples of the small blind
    const at = (frac: number) => Math.max(minTo, Math.min(maxTo,
      Math.round((s.current_bet + potAfterCall * frac) / unit) * unit))
    const opts = [
      { label: 'Min', to: minTo },
      { label: '1/2 pot', to: at(0.5) },
      { label: '3/4 pot', to: at(0.75) },
      { label: 'Pot', to: at(1) },
      { label: 'All-in', to: maxTo },
    ]
    return opts.filter((o, i) => i === 0 || i === opts.length - 1 || (o.to > minTo && o.to < maxTo))
  }, [s, canRaise, minTo, maxTo])

  const myTurn = !!p && !!s && sentId !== p.id
  const amount = Math.max(minTo, Math.min(raiseTo || minTo, maxTo))

  const send = async (action: string, amt?: number) => {
    if (!p || !s) return
    const label = action === 'fold' ? 'You folded' : action === 'check' ? 'You checked'
      : action === 'call' ? `You called ${fmt(toCall)}`
      : action === 'all_in' || amt === maxTo ? `You went all-in for ${fmt(maxTo)}`
      : `You ${s.current_bet > 0 ? 'raised to' : 'bet'} ${fmt(amt!)}`
    const previous = last
    setSentId(p.id)
    setLast(label)                                              // confirm straight away
    try {
      await api.act(token, { action, amount: amt, decision_id: p.id })
    } catch (e) { setSentId(null); setLast(previous); setError(String(e).replace(/^Error: /, '')) }
  }

  // Keyboard: F fold, C/X check or call, R edit the raise, Enter raise, A all-in.
  useEffect(() => {
    if (!myTurn) return
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement).tagName === 'INPUT'
      const k = e.key.toLowerCase()
      if (typing && k !== 'enter') return
      if (k === 'f' && !s!.can_check) send('fold')
      else if (k === 'c' || k === 'x') send(s!.can_check ? 'check' : 'call')
      else if (k === 'a') send('all_in')
      else if (k === 'r' && canRaise) { e.preventDefault(); raiseInput.current?.focus(); raiseInput.current?.select() }
      else if (k === 'enter' && canRaise) send(amount >= maxTo ? 'all_in' : 'raise', amount)
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (!seat) return null

  if (seat.finished) {
    return (
      <div className="dock done" data-testid="action-bar">
        <span className="dock-title">The match is over.</span>
        {result && <span className="sub num">You finished {result.place ? `#${result.place}` : ''} with{' '}
          {result.delta >= 0 ? '+' : ''}{fmt(result.delta)} chips.</span>}
        <span className="spacer" />
        <a href="#/new"><button className="primary">Play again</button></a>
      </div>
    )
  }

  if (!myTurn || !p || !s) {
    return (
      <div className="dock waiting" data-testid="action-bar">
        <span className="dock-dot" />
        <span className="sub">{last ? <><b>{last}.</b> </> : null}Waiting for the other players...</span>
        <span className="spacer" />
        <span className="muted small">You're playing as <b>{seat.name}</b></span>
      </div>
    )
  }

  const left = Math.max(0, p.time_left_ms - (now - seenAt))
  const urgent = left < 8000
  const odds = s.amount_owed ? Math.round((100 * toCall) / (s.pot + toCall)) : 0
  const raiseLabel = amount >= maxTo ? `All-in ${fmt(maxTo)}` : `${s.current_bet > 0 ? 'Raise to' : 'Bet'} ${fmt(amount)}`

  return (
    <div className={'dock mine' + (urgent ? ' urgent' : '')} data-testid="action-bar">
      <div className="dock-head">
        <span className="dock-title">Your turn</span>
        <span className="facts num">
          <span className="fact"><span className="k">To call</span><span className="v">{toCall ? fmt(toCall) : 'nothing'}</span></span>
          <span className="fact"><span className="k">Pot</span><span className="v">{fmt(s.pot)}</span></span>
          {toCall > 0 && <span className="fact"><span className="k">Pot odds</span><span className="v">{odds}%</span></span>}
          <span className="fact"><span className="k">Your stack</span><span className="v">{fmt(s.your_stack)}</span></span>
        </span>
        <span className="spacer" />
        <span className="timer num" title="Time left">{Math.ceil(left / 1000)}s</span>
      </div>
      <div className="timebar"><span style={{ width: `${(100 * left) / p.time_limit_ms}%` }} /></div>

      <div className="dock-actions">
        {s.can_check
          ? <button className="act" disabled title="Nothing to call: check instead">Fold<kbd>F</kbd></button>
          : <button className="act fold" onClick={() => send('fold')} data-testid="act-fold">Fold<kbd>F</kbd></button>}
        {s.can_check
          ? <button className="act main" onClick={() => send('check')} data-testid="act-check">Check<kbd>C</kbd></button>
          : <button className="act main" onClick={() => send('call')} data-testid="act-call">
              {toCall >= s.your_stack ? `Call all-in ${fmt(toCall)}` : `Call ${fmt(toCall)}`}<kbd>C</kbd></button>}
        {canRaise
          ? <button className="act raise" onClick={() => send(amount >= maxTo ? 'all_in' : 'raise', amount)}
                    data-testid="act-raise">{raiseLabel}<kbd>Enter</kbd></button>
          : <button className="act raise" onClick={() => send('all_in')} data-testid="act-allin">All-in {fmt(maxTo)}<kbd>A</kbd></button>}
      </div>

      {canRaise && (
        <div className="sizing">
          <span className="muted small">Raise size</span>
          {sizes.map(o => (
            <button key={o.label} className={'chip-btn' + (amount === o.to ? ' on' : '')}
                    onClick={() => setRaiseTo(o.to)} data-testid={o.label === 'All-in' ? 'act-allin' : undefined}
                    title={`${actionLabel('raise', o.to)}`}>
              {o.label} <span className="num">{fmt(o.to)}</span>
            </button>
          ))}
          <input type="range" min={minTo} max={maxTo} step={Math.max(1, s.blinds.bb / 2)} value={amount}
                 onChange={e => setRaiseTo(Number(e.target.value))} aria-label="Raise to" />
          <input ref={raiseInput} type="number" min={minTo} max={maxTo} value={raiseTo || minTo}
                 onChange={e => setRaiseTo(Number(e.target.value))} data-testid="raise-amount" aria-label="Raise to amount" />
          <span className="muted small"><kbd>R</kbd> to type an amount</span>
        </div>
      )}
      {error && <div className="error-text">{error}</div>}
    </div>
  )
}
