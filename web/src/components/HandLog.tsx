import { actionLabel } from '../replay/state'
import type { PokerEvent } from '../types'
import { Cards } from './Card'

const fmt = (n: number) => n.toLocaleString('en-US')

// One readable line per event; null hides the event from the log.
function line(e: PokerEvent): { who: string; what: React.ReactNode; sys?: boolean; err?: string } | null {
  switch (e.type) {
    case 'post':
      return { who: e.bot, what: e.kind === 'ante' ? `Ante ${fmt(e.amount)}` : `Posts ${e.kind.toUpperCase()} ${fmt(e.amount)}` }
    case 'decision':
      return { who: e.bot, what: actionLabel(e.applied.action, e.applied.amount),
               err: e.error ? e.error.replace('_', ' ') : undefined }
    case 'deal':
      return { who: e.street[0].toUpperCase() + e.street.slice(1), sys: true,
               what: <Cards cards={e.street === 'flop' ? e.board : e.board.slice(-1)} /> }
    case 'showdown':
      return { who: 'Showdown', sys: true, what: Object.entries(e.ranks).map(([b, r]) => `${b}: ${r}`).join(', ') }
    case 'award':
      return { who: e.bot, what: `Wins ${fmt(e.amount)}${e.pot === 'side' ? ' (side pot)' : ''}` }
    case 'elimination':
      return { who: e.bot, what: `Busts out (place ${e.place})` }
    case 'level':
      return { who: 'Level ' + e.level, sys: true, what: `Blinds ${fmt(e.sb)}/${fmt(e.bb)}${e.ante ? ` ante ${fmt(e.ante)}` : ''}` }
    case 'match_end':
      return { who: 'Match over', sys: true, what: e.stopped.replace(/_/g, ' ') }
    default:
      return null
  }
}

type Props = {
  events: PokerEvent[]
  from: number       // first event index of the current hand
  cursor: number
  onPick: (index: number) => void
}

// The current hand's events up to the cursor (never ahead of it: no spoilers).
export function HandLog({ events, from, cursor, onPick }: Props) {
  const rows: React.ReactNode[] = []
  for (let i = Math.max(from, 0); i <= cursor && i < events.length; i++) {
    const e = events[i]
    if (e.type === 'hand_start') {
      rows.push(<div key={i} className="hand-head num">Hand {e.hand + 1} &middot; button seat {e.button + 1}</div>)
      continue
    }
    const l = line(e)
    if (!l) continue
    rows.push(
      <div key={i} className={'row' + (i === cursor ? ' now' : '') + (l.sys ? ' sys' : '')}
           onClick={() => onPick(i)} data-testid="log-row" data-type={e.type}>
        <span className="who" title={l.who}>{l.who}</span>
        <span className="num">{l.what}{l.err && <span className="err"> &middot; {l.err}</span>}</span>
      </div>,
    )
  }
  if (!rows.length) return <div className="empty">Nothing has happened yet.</div>
  return <div className="log" data-testid="hand-log">{rows}</div>
}
