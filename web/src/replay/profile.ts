// Player statistics (the same definitions as arena/profiles.py, which counts
// them over a whole run on the server). Here they are counted in the browser
// over one match, up to the moment being watched, for the match view's Stats tab.

import type { PokerEvent } from '../types'

export const BLUFF_EQUITY = 0.4

export type ProfileColumn = { key: string; title: string; help: string; unit: '%' | 'x' | 'ms' | 'bb' | 'chips' }
export const PROFILE_COLUMNS: ProfileColumn[] = [
  { key: 'bb_100', title: 'BB/100', unit: 'bb', help: 'Big blinds won per 100 hands, each hand counted in that hand\u2019s big blinds.' },
  { key: 'chips_100', title: 'Chips/100', unit: 'chips', help: 'Chips won per 100 hands.' },
  { key: 'vpip', title: 'VPIP', unit: '%', help: 'Voluntarily put money in the pot: % of hands it called or raised before the flop. High = loose, low = tight.' },
  { key: 'pfr', title: 'PFR', unit: '%', help: 'Preflop raise: % of hands it raised before the flop. Close to VPIP = aggressive; far below = calls a lot.' },
  { key: 'three_bet', title: '3-bet', unit: '%', help: '% of the time it re-raised when facing a single preflop raise. High = fights back.' },
  { key: 'four_bet', title: '4-bet', unit: '%', help: '% of the time it raised again when facing a 3-bet.' },
  { key: 'fold_to_3bet', title: 'Fold to 3-bet', unit: '%', help: '% of the time it folded when its opening raise was re-raised.' },
  { key: 'aggression', title: 'Agg factor', unit: 'x', help: 'Aggression factor: postflop bets and raises per call. Above 2 = drives the action; below 1 = mostly calls.' },
  { key: 'flop_cbet', title: 'Flop c-bet', unit: '%', help: 'As the preflop raiser, % of the time it bet the flop when nobody had bet yet.' },
  { key: 'fold_to_cbet', title: 'Fold to flop c-bet', unit: '%', help: '% of the time it folded when the preflop raiser bet the flop. High = gives up easily.' },
  { key: 'flop_check_raise', title: 'Flop check-raise', unit: '%', help: '% of the time it raised after checking the flop and seeing the opponent bet.' },
  { key: 'turn_cbet', title: 'Turn c-bet', unit: '%', help: 'After its flop c-bet was called, % of the time it bet the turn again.' },
  { key: 'fold_to_turn_cbet', title: 'Fold vs turn c-bet', unit: '%', help: '% of the time it folded to a turn c-bet (a second bet from the preflop raiser).' },
  { key: 'turn_delayed', title: 'Turn delayed bet', unit: '%', help: 'As the preflop raiser, after the flop was checked through: % of the time it bet the turn.' },
  { key: 'fold_to_delayed', title: 'Fold vs turn delay', unit: '%', help: '% of the time it folded to a delayed c-bet on the turn.' },
  { key: 'turn_probe', title: 'Turn probe', unit: '%', help: 'When the preflop raiser did not bet the flop and it was checked through: % of the time it bet the turn itself, nobody having bet.' },
  { key: 'river_cbet', title: 'River c-bet', unit: '%', help: 'After betting the flop and the turn, % of the time it bet the river too.' },
  { key: 'fold_to_river_cbet', title: 'Fold to river c-bet', unit: '%', help: '% of the time it folded to a river c-bet (a third bet from the preflop raiser).' },
  { key: 'wtsd', title: 'WTSD', unit: '%', help: 'Went to showdown: % of the hands where it saw the flop that reached showdown. High = hard to push off a hand.' },
  { key: 'won_at_sd', title: 'W$SD', unit: '%', help: 'Won at showdown: % of its showdowns that won chips. High = its calls and bets are well chosen.' },
  { key: 'bluff', title: 'Bluff', unit: '%', help: '% of its postflop bets and raises made with under 40% equity against a random hand, whether or not they worked. (The purple timeline markers are something else: bets that made the better hand fold.)' },
  { key: 'think_ms', title: 'Think (ms)', unit: 'ms', help: 'Average time per decision, in milliseconds.' },
]

export function showStat(v: number | null | undefined, unit: string): string {
  if (v == null) return '-'
  if (unit === '%') return `${v.toFixed(0)}%`
  if (unit === 'x') return v.toFixed(1)
  if (unit === 'bb') return `${v > 0 ? '+' : ''}${v.toFixed(1)}`
  if (unit === 'chips') return `${v > 0 ? '+' : ''}${Math.round(v).toLocaleString('en-US')}`
  return v < 10 ? v.toFixed(1) : v.toFixed(0)
}

type Counts = Record<string, number>
const zero = (): Counts => ({ hands: 0, vpip: 0, pfr: 0, three_bet_chances: 0, three_bets: 0, post_aggressive: 0,
  post_calls: 0, cbets_faced: 0, cbets_folded: 0, saw_flop: 0, showdowns: 0, won_showdowns: 0,
  aggressive_with_equity: 0, bluffs: 0, decisions: 0, think_ms_total: 0,
  net_chips: 0, net_bb: 0, four_bet_chances: 0, four_bets: 0, fold_3bet_chances: 0, fold_3bets: 0,
  flop_cbet_chances: 0, flop_cbets: 0, flop_cr_chances: 0, flop_crs: 0,
  turn_cbet_chances: 0, turn_cbets: 0, turn_cbets_faced: 0, turn_cbets_folded: 0,
  delayed_chances: 0, delayed_bets: 0, delayed_faced: 0, delayed_folded: 0, probe_chances: 0, probes: 0,
  river_cbet_chances: 0, river_cbets: 0, river_cbets_faced: 0, river_cbets_folded: 0 })

// A bet into an unopened street, by kind: its [chances, made] counters, and the
// [faced, folded] counters for the opponent's first answer (arena/profiles.py).
const BET: Record<string, [string, string]> = {
  flop_cbet: ['flop_cbet_chances', 'flop_cbets'], turn_cbet: ['turn_cbet_chances', 'turn_cbets'],
  delayed: ['delayed_chances', 'delayed_bets'], probe: ['probe_chances', 'probes'],
  river_cbet: ['river_cbet_chances', 'river_cbets'] }
const FACED: Record<string, [string, string]> = {
  turn_cbet: ['turn_cbets_faced', 'turn_cbets_folded'], delayed: ['delayed_faced', 'delayed_folded'],
  river_cbet: ['river_cbets_faced', 'river_cbets_folded'] }

function streetStats(k: Counts, hand: any, street: string, b: string, a: string, aggressive: boolean) {
  const s = (hand.streets[street] ??= { bets: 0, first: null, checked: new Set(), acted: new Set(), kind: null, answered: false })
  const pfa = hand.pfRaiser
  if (s.bets === 0 && !s.acted.has(b) && pfa !== null) {
    const flop = hand.streets.flop
    let kind: string | null = null
    if (street === 'flop' && b === pfa) kind = 'flop_cbet'
    else if (street === 'turn' && b === pfa && hand.cbets.has('flop')) kind = 'turn_cbet'
    else if (street === 'turn' && flop && flop.bets === 0) kind = b === pfa ? 'delayed' : 'probe'
    else if (street === 'river' && b === pfa && hand.cbets.has('turn')) kind = 'river_cbet'
    if (kind) {
      k[BET[kind][0]] += 1
      if (aggressive) {
        k[BET[kind][1]] += 1
        s.kind = kind
        if (kind.endsWith('cbet')) hand.cbets.add(street)
      }
    }
  } else if (s.bets === 1 && b !== s.first && !s.answered) {
    s.answered = true
    if (street === 'flop' && s.checked.has(b)) { k.flop_cr_chances += 1; if (aggressive) k.flop_crs += 1 }
    const faced = FACED[s.kind]
    if (faced) { k[faced[0]] += 1; if (a === 'fold') k[faced[1]] += 1 }
  }
  if (a === 'check') s.checked.add(b)
  if (aggressive) { s.bets += 1; if (s.first === null) s.first = b }
  s.acted.add(b)
}

// Counts per bot over events[0..upTo]. A hand's preflop and showdown figures
// count when the hand ends, as on the server.
export function matchCounts(events: PokerEvent[], upTo: number): Record<string, Counts> {
  const c: Record<string, Counts> = {}
  const get = (b: string) => (c[b] ??= zero())
  let hand: any = null
  const last = Math.min(upTo, events.length - 1)
  for (let i = 0; i <= last; i++) {
    const e: any = events[i]
    if (e.type === 'hand_start') {
      hand = { order: e.order ?? Object.keys(e.hole ?? {}), folded: new Set(), raises: 0, lastRaiser: null,
               pfRaiser: null, vpip: new Set(), pfr: new Set(), flopBettor: null, cbetOpen: false,
               atShowdown: new Set(), won: new Set(), bb: e.blinds?.bb ?? 0, firstRaiser: null,
               streets: {}, cbets: new Set() }
      for (const p of hand.order) get(p).hands += 1
    } else if (!hand) {
      continue
    } else if (e.type === 'decision') {
      const b = e.bot, a = e.applied?.action, amount = e.applied?.amount ?? 0
      const cur = e.state?.current_bet ?? 0
      const aggressive = a === 'raise' || (a === 'all_in' && amount > cur)
      const calling = a === 'call' || (a === 'all_in' && amount <= cur)
      const k = get(b)
      k.decisions += 1
      if (typeof e.think_ms === 'number') k.think_ms_total += e.think_ms
      if (e.street === 'preflop') {
        if (aggressive || calling) hand.vpip.add(b)
        if (hand.raises === 1 && hand.lastRaiser !== b) { k.three_bet_chances += 1; if (aggressive) k.three_bets += 1 }
        if (hand.raises === 2 && hand.lastRaiser !== b) {
          k.four_bet_chances += 1; if (aggressive) k.four_bets += 1
          if (hand.firstRaiser === b) { k.fold_3bet_chances += 1; if (a === 'fold') k.fold_3bets += 1 }
        }
        if (aggressive) {
          if (hand.raises === 0) hand.firstRaiser = b
          hand.pfr.add(b); hand.raises += 1; hand.lastRaiser = hand.pfRaiser = b
        }
      } else {
        streetStats(k, hand, e.street, b, a, aggressive)
        if (aggressive) k.post_aggressive += 1
        if (calling) k.post_calls += 1
        if (aggressive && typeof e.equity === 'number') {
          k.aggressive_with_equity += 1
          if (e.equity < BLUFF_EQUITY) k.bluffs += 1
        }
        if (e.street === 'flop') {
          if (hand.flopBettor === null && aggressive) { hand.flopBettor = b; hand.cbetOpen = b === hand.pfRaiser }
          else if (hand.cbetOpen && b !== hand.flopBettor) {
            k.cbets_faced += 1
            if (a === 'fold') k.cbets_folded += 1
            hand.cbetOpen = false
          }
        }
      }
      if (a === 'fold') hand.folded.add(b)
    } else if (e.type === 'deal' && e.street === 'flop') {
      for (const p of hand.order) if (!hand.folded.has(p)) get(p).saw_flop += 1
    } else if (e.type === 'showdown') {
      hand.atShowdown = new Set(Object.keys(e.reveal ?? {}))
    } else if (e.type === 'award') {
      if (hand.atShowdown.has(e.bot) && (e.amount ?? 0) > 0) hand.won.add(e.bot)
    } else if (e.type === 'hand_end') {
      for (const p of hand.vpip) get(p).vpip += 1
      for (const p of hand.pfr) get(p).pfr += 1
      for (const p of hand.atShowdown) get(p).showdowns += 1
      for (const p of hand.won) get(p).won_showdowns += 1
      for (const [p, d] of Object.entries(e.deltas ?? {}) as [string, number][]) {
        get(p).net_chips += d
        if (hand.bb) get(p).net_bb += d / hand.bb
      }
      hand = null
    }
  }
  return c
}

export function profileOf(k: Counts) {
  const ratio = (a: string, b: string, pct = true) => !k[b] ? null
    : pct ? Math.round(1000 * k[a] / k[b]) / 10 : Math.round(100 * k[a] / k[b]) / 100
  const per100 = (a: string) => k.hands ? Math.round(1000 * k[a] / k.hands) / 10 : null
  return {
    hands: k.hands, bb_100: per100('net_bb'), chips_100: per100('net_chips'),
    vpip: ratio('vpip', 'hands'), pfr: ratio('pfr', 'hands'),
    three_bet: ratio('three_bets', 'three_bet_chances'), four_bet: ratio('four_bets', 'four_bet_chances'),
    fold_to_3bet: ratio('fold_3bets', 'fold_3bet_chances'), aggression: ratio('post_aggressive', 'post_calls', false),
    flop_cbet: ratio('flop_cbets', 'flop_cbet_chances'), fold_to_cbet: ratio('cbets_folded', 'cbets_faced'),
    flop_check_raise: ratio('flop_crs', 'flop_cr_chances'),
    turn_cbet: ratio('turn_cbets', 'turn_cbet_chances'), fold_to_turn_cbet: ratio('turn_cbets_folded', 'turn_cbets_faced'),
    turn_delayed: ratio('delayed_bets', 'delayed_chances'), fold_to_delayed: ratio('delayed_folded', 'delayed_faced'),
    turn_probe: ratio('probes', 'probe_chances'),
    river_cbet: ratio('river_cbets', 'river_cbet_chances'), fold_to_river_cbet: ratio('river_cbets_folded', 'river_cbets_faced'),
    wtsd: ratio('showdowns', 'saw_flop'), won_at_sd: ratio('won_showdowns', 'showdowns'),
    bluff: ratio('bluffs', 'aggressive_with_equity'),
    think_ms: k.decisions ? Math.round(10 * k.think_ms_total / k.decisions) / 10 : null,
  } as Record<string, number | null>
}
