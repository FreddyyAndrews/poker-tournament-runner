import { useEffect, useState } from 'react'
import { api, type SeatView } from '../api'
import { Cards } from './Card'

const fmt = (n: number) => n.toLocaleString('en-US')

// Shown when you hold a seat and it is your turn: actions, raise sizing, countdown.
export function ActionBar({ token, seat }: { token: string; seat: SeatView | null }) {
  const p = seat?.pending
  const s = p?.state
  const maxTo = s ? s.your_stack + s.your_bet_this_street : 0
  const minTo = s ? Math.min(s.min_raise_to, maxTo) : 0
  const [raiseTo, setRaiseTo] = useState(0)
  const [sent, setSent] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())

  useEffect(() => { if (p) { setRaiseTo(minTo); setError(null) } }, [p?.id])  // new decision
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 200); return () => clearInterval(t) }, [])
  const [seenAt, setSeenAt] = useState(Date.now())
  useEffect(() => { setSeenAt(Date.now()) }, [p?.time_left_ms])

  if (!seat) return null
  if (seat.finished) return <div className="action-bar"><span className="sub">You were seated as <b>{seat.name}</b>. The match is over.</span></div>
  if (!p || !s || sent === p.id) {
    return <div className="action-bar waiting" data-testid="action-bar">
      <span className="sub">Playing as <b>{seat.name}</b>. Waiting for the other players...</span></div>
  }

  const left = Math.max(0, p.time_left_ms - (now - seenAt))   // time_left_ms was measured at seenAt
  const send = async (action: string, amount?: number) => {
    try {
      setSent(p.id)
      await api.act(token, { action, amount, decision_id: p.id })
    } catch (e) { setSent(null); setError(String(e).replace(/^Error: /, '')) }
  }
  const potRaise = Math.min(maxTo, s.current_bet + s.pot + s.amount_owed)
  const halfPot = Math.min(maxTo, s.current_bet + Math.round((s.pot + s.amount_owed) / 2))
  const canRaise = s.your_stack > s.amount_owed && maxTo > s.current_bet

  return (
    <div className="action-bar mine" data-testid="action-bar">
      <div className="ab-top">
        <span className="you">Your turn</span>
        <Cards cards={s.your_cards} size="sm" />
        <span className="sub num">Pot {fmt(s.pot)} &middot; to call {fmt(s.amount_owed)} &middot; stack {fmt(s.your_stack)}</span>
        <span className="spacer" />
        <span className="num timer" title="Time left">{Math.ceil(left / 1000)}s</span>
      </div>
      <div className="timebar"><span style={{ width: `${(100 * left) / p.time_limit_ms}%` }} /></div>
      <div className="ab-actions">
        {s.can_check
          ? <button onClick={() => send('check')} data-testid="act-check">Check</button>
          : <><button onClick={() => send('fold')} data-testid="act-fold">Fold</button>
              <button onClick={() => send('call')} data-testid="act-call">Call {fmt(Math.min(s.amount_owed, s.your_stack))}</button></>}
        {canRaise && (
          <span className="raise">
            <input type="range" min={minTo} max={maxTo} step={Math.max(1, s.blinds.bb / 2)} value={raiseTo}
                   onChange={e => setRaiseTo(Number(e.target.value))} aria-label="Raise to" />
            <input type="number" min={minTo} max={maxTo} value={raiseTo} onChange={e => setRaiseTo(Number(e.target.value))}
                   data-testid="raise-amount" />
            <button className="ghost small" onClick={() => setRaiseTo(minTo)}>Min</button>
            <button className="ghost small" onClick={() => setRaiseTo(Math.max(minTo, halfPot))}>1/2 pot</button>
            <button className="ghost small" onClick={() => setRaiseTo(Math.max(minTo, potRaise))}>Pot</button>
            <button onClick={() => send('raise', Math.max(minTo, Math.min(raiseTo, maxTo)))} data-testid="act-raise">
              {s.current_bet > 0 ? 'Raise to' : 'Bet'} {fmt(Math.max(minTo, Math.min(raiseTo, maxTo)))}</button>
          </span>
        )}
        <button onClick={() => send('all_in')} data-testid="act-allin">All-in {fmt(maxTo)}</button>
      </div>
      {error && <div className="error-text">{error}</div>}
    </div>
  )
}
