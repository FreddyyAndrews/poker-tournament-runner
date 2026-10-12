import { actionLabel } from '../replay/state'
import type { Decision } from '../types'
import { Cards } from './Card'

const fmt = (n: number) => n.toLocaleString('en-US')

// Everything about one decision: what the bot saw, what it returned, how the
// rules applied it, and its own output (logs, debug, errors).
export function Inspector({ d }: { d: Decision | null }) {
  if (!d) return <div className="empty">Step to an action, or click one in the hand log, to inspect a decision.</div>
  const s = d.state
  const raw = d.reply.action + (d.reply.amount !== undefined ? ` ${fmt(d.reply.amount)}` : '')
  const changed = d.reply.action !== d.applied.action ||
    (d.reply.amount !== undefined && d.reply.amount !== d.applied.amount && d.applied.action !== 'call')
  const opponents = s.players.filter(p => p.bot_id !== d.bot)
  return (
    <div data-testid="inspector">
      <div className="section">
        <div style={{ fontWeight: 600, fontSize: 15 }}>{d.bot}</div>
        <div className="sub num">
          Hand {d.hand + 1} &middot; {d.street} &middot; {actionLabel(d.applied.action, d.applied.amount)}
          {d.think_ms !== null && <> &middot; {d.think_ms < 1 ? d.think_ms.toFixed(2) : Math.round(d.think_ms)} ms</>}
        </div>
      </div>

      {d.error && (
        <div className="section">
          <h2>Error</h2>
          <div className="error-text">{d.error.replace(/_/g, ' ')} (counted as a fold)</div>
          {d.error_detail && <pre className="block">{d.error_detail}</pre>}
        </div>
      )}

      <div className="section">
        <h2>What it saw</h2>
        <dl className="kv num">
          <dt>Cards</dt><dd><Cards cards={s.your_cards} /></dd>
          <dt>Board</dt><dd>{s.community_cards.length ? <Cards cards={s.community_cards} /> : <span className="muted">none</span>}</dd>
          <dt>Pot</dt><dd>{fmt(s.pot)}</dd>
          <dt>To call</dt><dd>{s.amount_owed ? fmt(s.amount_owed) : 'nothing (can check)'}</dd>
          <dt>Min raise to</dt><dd>{fmt(s.min_raise_to)}</dd>
          <dt>Stack</dt><dd>{fmt(s.your_stack)}</dd>
          <dt>Blinds</dt><dd>{fmt(s.blinds.sb)}/{fmt(s.blinds.bb)}{s.blinds.ante ? ` ante ${fmt(s.blinds.ante)}` : ''}</dd>
          <dt>Position</dt><dd>seat {s.seat_to_act + 1}{s.seat_to_act === s.button_seat ? ' (button)' :
            s.seat_to_act === s.sb_seat ? ' (small blind)' : s.seat_to_act === s.bb_seat ? ' (big blind)' : ''}</dd>
          <dt>Opponents</dt><dd>{opponents.map(p =>
            `${p.bot_id} ${fmt(p.stack)}${p.is_folded ? ' folded' : p.is_all_in ? ' all-in' : ''}`).join(', ')}</dd>
        </dl>
      </div>

      <div className="section">
        <h2>What it did</h2>
        <dl className="kv num">
          <dt>Returned</dt><dd className="mono">{raw}</dd>
          <dt>Applied as</dt><dd>{actionLabel(d.applied.action, d.applied.amount)}
            {changed && <span className="muted"> (adjusted by the rules)</span>}</dd>
          <dt>Pot after</dt><dd>{fmt(d.applied.pot_after)}</dd>
          <dt>Stack after</dt><dd>{fmt(d.applied.stack_after)}</dd>
        </dl>
      </div>

      <div className="section">
        <h2>Logs{d.logs_truncated ? ' (truncated)' : ''}</h2>
        {d.logs ? <pre className="block" data-testid="inspector-logs">{d.logs}</pre>
          : <div className="empty">The bot printed nothing.</div>}
      </div>

      <div className="section">
        <h2>Debug</h2>
        {d.debug !== null && d.debug !== undefined
          ? <pre className="block">{JSON.stringify(d.debug, null, 2)}</pre>
          : d.debug_error ? <div className="error-text">{d.debug_error}</div>
          : <div className="empty">No debug value returned.</div>}
      </div>

      <details className="section">
        <summary className="muted" style={{ cursor: 'pointer' }}>Full state sent to the bot</summary>
        <pre className="block">{JSON.stringify(s, null, 2)}</pre>
      </details>
    </div>
  )
}
