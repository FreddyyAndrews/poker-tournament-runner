// Table state from events. `apply` is pure; `Timeline` keeps a snapshot at
// every hand_start so seeking anywhere replays at most one hand.

import type { Blinds, Decision, MatchStart, PokerEvent, Result } from '../types'

export type PlayerState = {
  name: string
  seat: number
  stack: number
  bet: number               // chips in front of the player this street
  cards: string[] | null
  inHand: boolean           // dealt into the current hand
  folded: boolean
  allIn: boolean
  busted: boolean
  place: number | null
  lastAction: string | null // label shown by the seat, e.g. "Raise 600"
  won: number               // awarded this hand
  rank: string | null       // hand name at showdown
  shown: boolean            // cards revealed at showdown
  net: number               // chips won over the match so far (sum of hand deltas); the score in cash games
  score: number | null      // against an external bot: luck-adjusted chips won so far (sum of hand scores)
}

export type TableState = {
  start: MatchStart | null
  seatCount: number
  players: Record<string, PlayerState>
  hand: number | null
  level: number | null
  blinds: Blinds | null
  button: number | null
  sb: number | null
  bb: number | null
  board: string[]
  street: string
  pot: number               // chips already in the middle (excludes current bets)
  acting: string | null     // bot whose decision is the current event
  lastDecision: Decision | null
  handOver: boolean
  results: Result[] | null
  stopped: string | null
}

export const EMPTY: TableState = {
  start: null, seatCount: 0, players: {}, hand: null, level: null, blinds: null,
  button: null, sb: null, bb: null, board: [], street: 'preflop', pot: 0, acting: null,
  lastDecision: null, handOver: false, results: null, stopped: null,
}

const fmt = (n: number) => n.toLocaleString('en-US')

export function actionLabel(action: string, amount: number): string {
  switch (action) {
    case 'fold': return 'Fold'
    case 'check': return 'Check'
    case 'call': return `Call ${fmt(amount)}`
    case 'raise': return `Raise to ${fmt(amount)}`
    case 'all_in': return `All-in ${fmt(amount)}`
    default: return action
  }
}

function mapPlayers(s: TableState, fn: (p: PlayerState) => PlayerState): Record<string, PlayerState> {
  const out: Record<string, PlayerState> = {}
  for (const [k, p] of Object.entries(s.players)) out[k] = fn(p)
  return out
}

function update(s: TableState, name: string, patch: Partial<PlayerState>): TableState {
  const p = s.players[name]
  if (!p) return s
  return { ...s, players: { ...s.players, [name]: { ...p, ...patch } } }
}

export function apply(s: TableState, e: PokerEvent): TableState {
  switch (e.type) {
    case 'match_start': {
      const players: Record<string, PlayerState> = {}
      e.seats.forEach((seat, i) => {
        players[seat.name] = {
          name: seat.name, seat: seat.seat ?? i, stack: e.config.starting_stack, bet: 0,
          cards: null, inHand: false, folded: false, allIn: false, busted: false, place: null,
          lastAction: null, won: 0, rank: null, shown: false, net: 0, score: null,
        }
      })
      const seatCount = e.table_size ?? Math.max(2, ...Object.values(players).map(p => p.seat + 1))
      return { ...EMPTY, start: e, seatCount, players }
    }
    case 'level':
      return { ...s, level: e.level, blinds: { sb: e.sb, bb: e.bb, ante: e.ante } }
    case 'hand_start': {
      // Players who busted or moved away last hand leave the table now.
      const staying: Record<string, PlayerState> = {}
      for (const [k, p] of Object.entries(s.players)) if (!p.busted || e.order.includes(k)) staying[k] = p
      s = { ...s, players: staying }
      const players = mapPlayers(s, p => {
        const inHand = e.order.includes(p.name)
        return {
          ...p, inHand, bet: 0, folded: false, allIn: false, lastAction: null, won: 0,
          rank: null, shown: false,
          seat: e.seats[p.name] ?? p.seat,
          stack: e.stacks[p.name] ?? p.stack,
          cards: e.hole[p.name] ?? null,
        }
      })
      return {
        ...s, players, hand: e.hand, level: e.level, blinds: e.blinds, button: e.button,
        sb: e.sb, bb: e.bb, board: [], street: 'preflop', pot: 0, acting: null,
        lastDecision: null, handOver: false,
      }
    }
    case 'post': {
      const p = s.players[e.bot]
      if (!p) return s
      if (e.kind === 'ante') {
        return { ...update(s, e.bot, { stack: p.stack - e.amount, allIn: p.stack - e.amount === 0 }), pot: s.pot + e.amount }
      }
      return update(s, e.bot, {
        stack: p.stack - e.amount, bet: p.bet + e.amount, allIn: p.stack - e.amount === 0,
        lastAction: e.kind === 'sb' ? `SB ${fmt(e.amount)}` : `BB ${fmt(e.amount)}`,
      })
    }
    case 'decision': {
      const p = s.players[e.bot]
      if (!p) return s
      const paid = p.stack - e.applied.stack_after
      const next = update(s, e.bot, {
        stack: e.applied.stack_after,
        bet: p.bet + paid,
        folded: e.applied.action === 'fold',
        allIn: e.applied.action !== 'fold' && e.applied.stack_after === 0,
        lastAction: actionLabel(e.applied.action, e.applied.action === 'call' ? paid : e.applied.amount),
      })
      return { ...next, acting: e.bot, lastDecision: e }
    }
    case 'deal': {
      // Bets go into the pot when the next street is dealt.
      const swept = Object.values(s.players).reduce((t, p) => t + p.bet, 0)
      const players = mapPlayers(s, p => ({ ...p, bet: 0, lastAction: p.folded ? p.lastAction : null }))
      return { ...s, players, pot: s.pot + swept, board: e.board, street: e.street, acting: null }
    }
    case 'showdown': {
      const swept = Object.values(s.players).reduce((t, p) => t + p.bet, 0)
      const players = mapPlayers(s, p => e.reveal[p.name]
        ? { ...p, bet: 0, cards: e.reveal[p.name], shown: true, rank: e.ranks[p.name] ?? null }
        : { ...p, bet: 0 })
      return { ...s, players, pot: s.pot + swept, board: e.board, acting: null }
    }
    case 'award': {
      const swept = Object.values(s.players).reduce((t, p) => t + p.bet, 0)
      const players = mapPlayers(s, p => ({ ...p, bet: 0 }))
      const w = players[e.bot]
      if (w) players[e.bot] = { ...w, won: w.won + e.amount, stack: w.stack + e.amount }
      return { ...s, players, pot: Math.max(0, s.pot + swept - e.amount), acting: null }
    }
    case 'hand_end': {
      const scores = e.score ?? e.aivat ?? {}
      const oppHole = e.opp_hole ?? (e.gtow_hole ? { gto_wizard: e.gtow_hole } : {})
      const players = mapPlayers(s, p => e.stacks[p.name] === undefined ? p : {
        ...p, stack: e.stacks[p.name], bet: 0, net: p.net + (e.deltas[p.name] ?? 0),
        score: scores[p.name] != null ? (p.score ?? 0) + (scores[p.name] as number) : p.score,
        // an external bot's cards are only known once the hand is over
        cards: p.cards ?? (oppHole[p.name]?.length ? oppHole[p.name] : null),
      })
      return { ...s, players, pot: 0, acting: null, handOver: true }
    }
    case 'elimination':
      return update(s, e.bot, { busted: true, place: e.place, inHand: false })
    case 'seat_change': {
      if (e.reason === 'join') {
        const p: PlayerState = s.players[e.bot] ?? {
          name: e.bot, seat: e.seat, stack: e.stack, bet: 0, cards: null, inHand: false, folded: false,
          allIn: false, busted: false, place: null, lastAction: null, won: 0, rank: null, shown: false, net: 0,
          score: null,
        }
        return { ...s, players: { ...s.players, [e.bot]: { ...p, seat: e.seat, stack: e.stack, busted: false,
                                                             lastAction: 'Joined' } } }
      }
      if (e.reason === 'bust') return update(s, e.bot, { busted: true, inHand: false })
      // moved to another table (balancing or a table break)
      const players = { ...s.players }
      delete players[e.bot]
      return { ...s, players }
    }
    case 'match_end': {
      let next: TableState = { ...s, results: e.results, stopped: e.stopped, acting: null }
      for (const r of e.results) next = update(next, r.bot, { place: r.place })
      return next
    }
    default:
      return s
  }
}

// Incrementally built states with a snapshot at every hand_start.
export class Timeline {
  private events: PokerEvent[] = []
  private snapshots: { index: number; state: TableState }[] = [{ index: -1, state: EMPTY }]
  private last: TableState = EMPTY

  get length() { return this.events.length }
  get list(): PokerEvent[] { return this.events }

  append(events: PokerEvent[]) {
    for (const e of events) {
      const i = this.events.length
      if (e.type === 'hand_start') this.snapshots.push({ index: i - 1, state: this.last })
      this.events.push(e)
      this.last = apply(this.last, e)
    }
  }

  // State after applying events[0..index] (index -1 = before anything).
  stateAt(index: number): TableState {
    if (index >= this.events.length - 1) return this.last
    let lo = 0, hi = this.snapshots.length - 1
    while (lo < hi) {               // last snapshot with snap.index <= index
      const mid = (lo + hi + 1) >> 1
      if (this.snapshots[mid].index <= index) lo = mid
      else hi = mid - 1
    }
    let { index: i, state } = this.snapshots[lo]
    while (i < index) state = apply(state, this.events[++i])
    return state
  }
}
