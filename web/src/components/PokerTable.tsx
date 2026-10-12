import type { TableState } from '../replay/state'
import { Card } from './Card'

export const playerColor = (i: number) => `var(--p${Math.min(i, 8)})`

const fmt = (n: number) => n.toLocaleString('en-US')

export function ord(n: number): string {
  const teen = n % 100 >= 11 && n % 100 <= 13
  return n + (teen ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th')
}

// Seat positions around the oval, as percentages of the table box.
// Seat 0 is bottom centre; seats go clockwise.
function seatPos(seat: number, count: number) {
  const a = Math.PI / 2 + (seat * 2 * Math.PI) / count
  return { x: 50 + 44 * Math.cos(a), y: 50 + 41 * Math.sin(a) }
}

function toward(p: { x: number; y: number }, t: number) {
  return { x: 50 + (p.x - 50) * t, y: 47 + (p.y - 47) * t }
}

type Props = {
  state: TableState
  showAll: boolean         // spectator view: every hole card face up
  colorOf: (name: string) => string
  me?: string              // playing as this seat: always show its cards
  waitingFor?: string      // a human/LLM seat the table is waiting on
}

export function PokerTable({ state, showAll, colorOf, me, waitingFor }: Props) {
  const players = Object.values(state.players)
  const count = Math.max(state.seatCount, 2)
  const winners = new Set(players.filter(p => p.won > 0).map(p => p.name))

  return (
    <div className="table" data-testid="poker-table">
      <div className="felt" />
      <div className="center">
        <div className="hand-info num" data-testid="hand-info">
          {state.hand !== null ? <>
            HAND {state.hand + 1}
            {state.blinds && <> &middot; BLINDS {fmt(state.blinds.sb)}/{fmt(state.blinds.bb)}
              {state.blinds.ante ? ` ANTE ${fmt(state.blinds.ante)}` : ''}</>}
            {state.level ? <> &middot; LEVEL {state.level}</> : null}
          </> : '\u00a0'}
        </div>
        <div className="board" data-testid="board">
          {[0, 1, 2, 3, 4].map(i => <Card key={i + (state.board[i] ?? '')} card={state.board[i]} size="lg" />)}
        </div>
        <div className="pot num" data-testid="pot">
          {state.pot > 0 ? <>Pot {fmt(state.pot)}</> : <span className="muted">&nbsp;</span>}
        </div>
      </div>

      {players.map(p => {
        const pos = seatPos(p.seat, count)
        const betPos = toward(pos, 0.56)
        const hidden = !showAll && !p.shown && p.name !== me
        const cls = ['seat',
          (waitingFor ? waitingFor === p.name : state.acting === p.name) ? 'acting' : '',
          p.folded ? 'folded' : '',
          p.busted || (!p.inHand && state.hand !== null) ? 'out' : '',
          winners.has(p.name) ? 'winner' : ''].filter(Boolean).join(' ')
        const badge = state.button === p.seat ? 'D' : null
        return (
          <div key={p.name}>
            <div className={cls} style={{ left: `${pos.x}%`, top: `${pos.y}%` }}
                 data-testid={`seat-${p.name}`}>
              <div className="cards">
                {p.inHand && p.cards && !(p.folded && hidden) && p.cards.map((c, i) =>
                  <Card key={i + c} card={c} back={hidden} />)}
                {p.inHand && !p.cards && !p.folded && <><Card back /><Card back /></>}
              </div>
              <div className="plate">
                <span className="swatch" style={{ background: colorOf(p.name) }} />
                <span className="name" title={p.name}>{p.name}</span>
                {badge ? <span className="badge" title="Dealer button">{badge}</span> : <span />}
                <span className="stack num">
                  {p.busted && p.place ? `Out · ${ord(p.place)}` : fmt(p.stack)}
                </span>
              </div>
              <div className="say num">
                {p.won > 0 ? <span className="won">+{fmt(p.won)}{p.rank ? ` · ${p.rank}` : ''}</span>
                  : p.rank ? <span className="hand-rank">{p.rank}</span>
                  : p.lastAction}
              </div>
            </div>
            {p.bet > 0 && (
              <div className="bet num" style={{ left: `${betPos.x}%`, top: `${betPos.y}%` }}>{fmt(p.bet)}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}
