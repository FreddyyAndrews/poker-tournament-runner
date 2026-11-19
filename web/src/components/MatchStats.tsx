import { useMemo } from 'react'
import type { PokerEvent } from '../types'
import { PROFILE_COLUMNS, matchCounts, profileOf, showStat } from '../replay/profile'

// This match's player statistics up to the moment being watched: the same
// stats as the qualifier's player profiles, for this matchup only. Built from
// the events up to the cursor, so a live match or broadcast gives nothing away.
export function MatchStats({ events, cursor, names, colorOf }: {
  events: PokerEvent[]; cursor: number; names: string[]; colorOf: (n: string) => string
}) {
  const profiles = useMemo(() => {
    const counts = matchCounts(events, cursor)
    return Object.fromEntries(names.map(n => [n, counts[n] ? profileOf(counts[n]) : null]))
  }, [events, cursor, names])
  const hands = Math.max(0, ...names.map(n => profiles[n]?.hands ?? 0))
  return (
    <div className="match-stats" data-testid="match-stats">
      <table className="list num">
        <thead><tr><th />{names.map(n => (
          <th key={n} className="r"><span className="who"><span className="swatch" style={{ background: colorOf(n) }} />{n}</span></th>
        ))}</tr></thead>
        <tbody>
          <tr><td title="Hands dealt so far">Hands</td>{names.map(n => <td key={n} className="r">{profiles[n]?.hands ?? 0}</td>)}</tr>
          {PROFILE_COLUMNS.map(c => (
            <tr key={c.key}><td title={c.help} className="stat-name">{c.title}</td>
              {names.map(n => <td key={n} className="r">{showStat(profiles[n]?.[c.key], c.unit)}</td>)}</tr>
          ))}
        </tbody>
      </table>
      <p className="hint">This match only, up to the hand on screen{hands < 20 ? ': few hands so far, so these will move a lot' : ''}.
        Hover over a stat for what it measures.</p>
    </div>
  )
}
