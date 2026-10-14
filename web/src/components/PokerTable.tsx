import { useEffect, useRef, useState } from 'react'
import type { TableState } from '../replay/state'
import { Card } from './Card'
import { ChipIcon, ChipStack, denominationsIn } from './Chips'

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

type Pt = { x: number; y: number }

// Where a player's chips sit. Along the long top and bottom edges: beside the
// nameplate (on the side away from the right edge, so neighbouring piles never
// meet). At the short ends a pile beside the plate would sit in the middle of
// the table, so it goes above the seat's cards (upper half) or below its action
// line (lower half), away from the other seat on that end. The seat is a fixed
// pixel size, so those offsets are in pixels; `at` is the approximate point in
// table percentages, used for chip flights.
const END_GAP_PX = 76          // seat centre to just past its cards / action line
function isEnd(pos: Pt) { return Math.abs(pos.x - 50) > 30 }
function stackPos(pos: Pt): Pt {
  if (isEnd(pos)) return { x: pos.x, y: pos.y + (pos.y < 47 ? -16 : 16) }
  return { x: pos.x + (pos.x > 75 ? -13.5 : 13.5), y: pos.y + 3.5 }
}
function pileStyle(pos: Pt): React.CSSProperties {
  if (!isEnd(pos)) {
    const sp = stackPos(pos)
    return { left: `${sp.x}%`, top: `${sp.y}%` }
  }
  return pos.y < 47
    ? { left: `${pos.x}%`, top: `calc(${pos.y}% - ${END_GAP_PX}px)` }                         // above: anchored at its bottom
    : { left: `${pos.x}%`, top: `calc(${pos.y}% + ${END_GAP_PX}px)`, transform: 'translate(-50%, 0)' }   // below: at its top
}
const POT_POS: Pt = { x: 46, y: 60 }   // where the pot's chips sit, left of the label
// The table box is 2 : 1.08, so 1% of its height is 0.54% of its width.
const H_PER_W = 1.08 / 2

type Flight = { id: number; from: Pt; to: Pt; amount: number }
let flightId = 0

/** Chip movements between two states of the same hand: bets going in, bets
 * swept into the pot, antes, and the pot going to the winner. */
function flightsBetween(prev: TableState, next: TableState, seatOf: (seat: number) => Pt): Flight[] {
  if (prev.hand === null || prev.hand !== next.hand) return []
  const out: Flight[] = []
  const add = (from: Pt, to: Pt, amount: number) => { if (amount > 0) out.push({ id: ++flightId, from, to, amount }) }
  for (const p of Object.values(next.players)) {
    const before = prev.players[p.name]
    if (!before) continue
    const pos = seatOf(p.seat)
    const bet = toward(pos, 0.48)
    const dBet = p.bet - before.bet
    if (dBet > 0) add(stackPos(pos), bet, dBet)                                  // a bet or call
    else if (before.bet > 0 && p.bet === 0) add(bet, POT_POS, before.bet)         // swept into the pot
    else if (dBet === 0 && before.stack > p.stack && next.pot > prev.pot && p.won === before.won)
      add(stackPos(pos), POT_POS, before.stack - p.stack)                        // an ante
    if (p.won > before.won) add(POT_POS, stackPos(pos), p.won - before.won)      // winnings
  }
  return out
}

type Props = {
  state: TableState
  showAll: boolean         // spectator view: every hole card face up
  colorOf: (name: string) => string
  me?: string              // playing as this seat: always show its cards
  waitingFor?: string      // a human/LLM seat the table is waiting on
  prev?: TableState | null // the state one event earlier, to animate chips from
  animMs?: number          // chip flight duration (0 = no animation)
}

export function PokerTable({ state, showAll, colorOf, me, waitingFor, prev, animMs = 0 }: Props) {
  const players = Object.values(state.players)
  const count = Math.max(state.seatCount, 2)
  const cash = state.start?.config.mode === 'cash'
  const winners = new Set(players.filter(p => p.won > 0).map(p => p.name))
  const [flights, setFlights] = useState<Flight[]>([])
  const timers = useRef<number[]>([])

  useEffect(() => {
    if (!prev || animMs <= 0) return
    const fresh = flightsBetween(prev, state, seat => seatPos(seat, count))
    if (!fresh.length) return
    setFlights(f => [...f, ...fresh])
    const ids = new Set(fresh.map(f => f.id))
    timers.current.push(window.setTimeout(() => setFlights(f => f.filter(x => !ids.has(x.id))), animMs + 60))
  }, [state])   // one animation per new state
  useEffect(() => () => timers.current.forEach(clearTimeout), [])

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
          {state.pot > 0
            ? <><span className="pot-chips" data-testid="pot-chips"><ChipStack amount={state.pot} maxPerColumn={12} maxColumns={5} /></span>
                <span>Pot {fmt(state.pot)}</span></>
            : <span className="muted">&nbsp;</span>}
        </div>
      </div>

      {players.map(p => {
        const pos = seatPos(p.seat, count)
        const betPos = toward(pos, 0.48)
        const hidden = !showAll && !p.shown && p.name !== me
        const cls = ['seat',
          (waitingFor ? waitingFor === p.name : state.acting === p.name) ? 'acting' : '',
          me === p.name ? 'me' : '',
          me === p.name && waitingFor === p.name ? 'my-turn' : '',
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
                  <Card key={i + c} card={c} back={hidden} size={me === p.name ? 'lg' : 'md'} />)}
                {p.inHand && !p.cards && !p.folded && <><Card back /><Card back /></>}
              </div>
              <div className="plate">
                <span className="swatch" style={{ background: colorOf(p.name) }} />
                <span className="name" title={p.name}>{p.name}{me === p.name && p.name.toLowerCase() !== 'you' && <span className="you-tag">YOU</span>}</span>
                {badge ? <span className="badge" title="Dealer button">{badge}</span> : <span />}
                <span className="stack num">
                  {p.busted && p.place ? `Out · ${ord(p.place)}` : fmt(p.stack)}
                  {cash && <span className="net" title="Net over the match" data-testid={`net-${p.name}`}>
                    {p.net === 0 ? '\u00b10' : (p.net > 0 ? '+' : '') + fmt(p.net)}</span>}
                </span>
              </div>
              <div className="say num">
                {p.won > 0 ? <span className="won">+{fmt(p.won)}{p.rank ? ` · ${p.rank}` : ''}</span>
                  : p.rank ? <span className="hand-rank">{p.rank}</span>
                  : p.lastAction}
              </div>
            </div>
            {p.stack > 0 && (
              <div className={'pile' + (p.folded || !p.inHand ? ' dim' : '')}
                   style={pileStyle(pos)} data-testid={`stack-${p.name}`}>
                <ChipStack amount={p.stack} maxPerColumn={8} />
              </div>
            )}
            {p.bet > 0 && (
              <div className="bet-pile" style={{ left: `${betPos.x}%`, top: `${betPos.y}%` }}
                   data-testid={`bet-${p.name}`}>
                <ChipStack amount={p.bet} maxPerColumn={8} maxColumns={3} />
                <span className="bet num">{fmt(p.bet)}</span>
              </div>
            )}
          </div>
        )
      })}
      {(() => {
        // Legend: only the chip values drawn on the table right now.
        const legend = denominationsIn([
          ...players.filter(p => p.stack > 0).map(p => [p.stack, 8, 4] as [number, number, number]),
          ...players.filter(p => p.bet > 0).map(p => [p.bet, 8, 3] as [number, number, number]),
          ...(state.pot > 0 ? [[state.pot, 12, 5] as [number, number, number]] : []),
          ...flights.map(f => [f.amount, 5, 3] as [number, number, number]),
        ])
        return legend.length > 0 && (
          <div className="chip-legend" data-testid="chip-legend" aria-label="Chip values">
            {legend.map(d => (
              <span key={d.value} className="item" data-value={d.value}>
                <ChipIcon d={d} /><span className="num">{d.label}</span>
              </span>
            ))}
          </div>
        )
      })()}
      {flights.map(f => (
        <div key={f.id} className="pile flight"
             style={{
               left: `${f.to.x}%`, top: `${f.to.y}%`,
               ['--dx' as string]: `${f.from.x - f.to.x}cqw`,
               ['--dy' as string]: `${(f.from.y - f.to.y) * H_PER_W}cqw`,
               ['--ms' as string]: `${animMs}ms`,
             }}>
          <ChipStack amount={f.amount} maxPerColumn={5} maxColumns={3} />
        </div>
      ))}
    </div>
  )
}
