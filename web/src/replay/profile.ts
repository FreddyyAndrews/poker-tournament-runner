// Player statistics (the same definitions as arena/profiles.py, which counts
// them over a whole run on the server). Here they are counted in the browser
// over one match, up to the moment being watched, for the match view's Stats tab.

import type { PokerEvent } from '../types'

export const BLUFF_EQUITY = 0.4

export type ProfileColumn = { key: string; title: string; help: string; unit: '%' | 'x' | 'ms' }
export const PROFILE_COLUMNS: ProfileColumn[] = [
  { key: 'vpip', title: 'VPIP', unit: '%', help: 'Voluntarily put money in the pot: % of hands it called or raised before the flop. High = loose, low = tight.' },
  { key: 'pfr', title: 'PFR', unit: '%', help: 'Preflop raise: % of hands it raised before the flop. Close to VPIP = aggressive; far below = calls a lot.' },
  { key: 'three_bet', title: '3-bet', unit: '%', help: '% of the time it re-raised when facing a single preflop raise. High = fights back.' },
  { key: 'aggression', title: 'Aggression', unit: 'x', help: 'Postflop bets and raises per call. Above 2 = drives the action; below 1 = mostly calls.' },
  { key: 'fold_to_cbet', title: 'Fold to c-bet', unit: '%', help: '% of the time it folded when the preflop raiser bet the flop. High = gives up easily.' },
  { key: 'wtsd', title: 'Showdown', unit: '%', help: 'Went to showdown: % of the hands where it saw the flop that reached showdown. High = hard to push off a hand.' },
  { key: 'won_at_sd', title: 'Won at SD', unit: '%', help: '% of its showdowns that won chips. High = its calls and bets are well chosen.' },
  { key: 'bluff', title: 'Bluff', unit: '%', help: '% of its postflop bets and raises made with under 40% equity against a random hand, whether or not they worked (the purple timeline markers are only the ones that got a fold).' },
  { key: 'think_ms', title: 'Think (ms)', unit: 'ms', help: 'Average time per decision, in milliseconds.' },
]

export function showStat(v: number | null | undefined, unit: string): string {
  if (v == null) return '-'
  if (unit === '%') return `${v.toFixed(0)}%`
  if (unit === 'x') return v.toFixed(1)
  return v < 10 ? v.toFixed(1) : v.toFixed(0)
}

type Counts = Record<string, number>
const zero = (): Counts => ({ hands: 0, vpip: 0, pfr: 0, three_bet_chances: 0, three_bets: 0, post_aggressive: 0,
  post_calls: 0, cbets_faced: 0, cbets_folded: 0, saw_flop: 0, showdowns: 0, won_showdowns: 0,
  aggressive_with_equity: 0, bluffs: 0, decisions: 0, think_ms_total: 0 })

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
               atShowdown: new Set(), won: new Set() }
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
        if (aggressive) { hand.pfr.add(b); hand.raises += 1; hand.lastRaiser = hand.pfRaiser = b }
      } else {
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
      hand = null
    }
  }
  return c
}

export function profileOf(k: Counts) {
  const ratio = (a: string, b: string, pct = true) => !k[b] ? null
    : pct ? Math.round(1000 * k[a] / k[b]) / 10 : Math.round(100 * k[a] / k[b]) / 100
  return {
    hands: k.hands, vpip: ratio('vpip', 'hands'), pfr: ratio('pfr', 'hands'),
    three_bet: ratio('three_bets', 'three_bet_chances'), aggression: ratio('post_aggressive', 'post_calls', false),
    fold_to_cbet: ratio('cbets_folded', 'cbets_faced'), wtsd: ratio('showdowns', 'saw_flop'),
    won_at_sd: ratio('won_showdowns', 'showdowns'), bluff: ratio('bluffs', 'aggressive_with_equity'),
    think_ms: k.decisions ? Math.round(10 * k.think_ms_total / k.decisions) / 10 : null,
  } as Record<string, number | null>
}
