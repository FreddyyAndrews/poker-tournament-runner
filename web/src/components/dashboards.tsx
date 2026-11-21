// Format-specific run dashboards: bracket tree, round-robin grid, multi-table
// overview, series results. Each takes the server's `progress` object.

import { useEffect, useRef, useState } from 'react'
import { ord } from './PokerTable'
import { PROFILE_COLUMNS, showStat } from '../replay/profile'

const fmt = (n: number) => (n ?? 0).toLocaleString('en-US')
const go = (key: string) => { location.hash = `#/match/${key}` }

function Live() {
  return <span className="pill live"><span className="dot" />Live</span>
}

// ---------------------------------------------------------------------------

export function BracketView({ p }: { p: any }) {
  return (
    <div className="bracket" data-testid="bracket">
      {p.rounds.map((r: any) => (
        <div className="round" key={r.round}>
          <div className="round-name">{r.name}</div>
          <div className="ties">
            {r.ties.map((t: any) => (
              <div key={t.match_id}
                   className={'tie' + (t.status === 'running' ? ' live' : '') + (t.bye ? ' bye' : '') +
                              (t.status !== 'pending' && !t.bye ? ' link' : '')}
                   onClick={() => t.status !== 'pending' && !t.bye && go(t.key)}
                   data-testid="tie" data-status={t.status}>
                {[0, 1].map(i => {
                  const name = t.players[i]
                  const won = t.winner && t.winner === name
                  const lost = t.winner && name && t.winner !== name
                  return (
                    <div key={i} className={'slot' + (won ? ' won' : '') + (lost ? ' lost' : '')}>
                      <span className="seed num">{t.seeds[i] ?? ''}</span>
                      <span className="who">{name ?? (t.bye ? 'bye' : 'TBD')}</span>
                      {/* cash: stacks reset every hand, so show chips won instead */}
                      <span className="num">{!name ? '' : p.cash && t.delta
                        ? (t.delta[name] > 0 ? '+' : t.delta[name] === 0 ? '\u00b1' : '') + fmt(t.delta[name])
                        : t.stacks ? fmt(t.stacks[name]) : ''}</span>
                    </div>
                  )
                })}
                {t.status === 'running' && <div className="tie-foot"><Live /> watch</div>}
                {t.how === 'coin_flip' && <div className="tie-foot muted">level on chips, coin flip</div>}
              </div>
            ))}
          </div>
        </div>
      ))}
      <div className="round">
        <div className="round-name">Champion</div>
        <div className="ties">
          <div className="tie champion"><div className="slot won"><span className="who">{p.champion ?? 'TBD'}</span></div></div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------

export function RoundRobinView({ p }: { p: any }) {
  const bots: string[] = p.bots
  const cell = (a: string, b: string) => p.pairings.find((x: any) =>
    (x.players[0] === a && x.players[1] === b) || (x.players[0] === b && x.players[1] === a))
  return (
    <div>
      <div className="rr-wrap">
        <table className="rr num" data-testid="rr-grid">
          <thead>
            <tr><th />{bots.map(b => <th key={b} title={b}>{b}</th>)}</tr>
          </thead>
          <tbody>
            {bots.map(a => (
              <tr key={a}>
                <th>{a}</th>
                {bots.map(b => {
                  if (a === b) return <td key={b} className="self" />
                  const x = cell(a, b)
                  const d = x.delta[a]
                  const res = x.status !== 'complete' ? null : x.winner === 'draw' ? 'D' : x.winner === a ? 'W' : 'L'
                  return (
                    <td key={b} className={'link ' + (res === 'W' ? 'win' : res === 'L' ? 'loss' : '')}
                        onClick={() => x.status !== 'pending' && go(x.legs[0].key)}
                        title={`${a} vs ${b}: ${x.status}`}>
                      {x.status === 'pending' ? <span className="muted">-</span>
                        : <>{res && <span className="res">{res}</span>}<span className="d">{d > 0 ? '+' : ''}{fmt(d)}</span>
                            {x.status === 'running' && <span className="live-dot" />}</>}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">Each cell: the row bot's result and chips won against the column bot
        {p.pairings[0]?.legs.length === 2 ? ', across both duplicate legs' : ''}. Click a cell to watch.</p>
    </div>
  )
}

// ---------------------------------------------------------------------------

export function MttView({ p }: { p: any }) {
  const [showClosed, setShowClosed] = useState(false)
  const open = p.tables.filter((t: any) => !t.closed)
  const closed = p.tables.filter((t: any) => t.closed)
  return (
    <div>
      <div className="mtt-facts num">
        <span><b>{p.players_left}</b> players left</span>
        <span><b>{open.length}</b> {open.length === 1 ? 'table' : 'tables'}</span>
        {p.chip_leader && <span>Chip leader <b>{p.chip_leader}</b></span>}
      </div>
      <div className="mtt-grid" data-testid="mtt-tables">
        {[...open, ...(showClosed ? closed : [])].map((t: any) => {
          const leader = t.players.reduce((m: any, x: any) => (!m || x.stack > m.stack ? x : m), null)
          return (
            <div key={t.table} className={'mtt-table' + (t.closed ? ' closed' : '')} data-testid="mtt-table">
              <div className="head">
                <span className="t">{open.length === 1 && !t.closed ? 'Final table' : `Table ${t.table}`}</span>
                {t.status === 'running' && <Live />}
                {t.closed && <span className="pill">Closed</span>}
                {t.status === 'incomplete' && <span className="pill">Didn't finish</span>}
                <span className="spacer" />
                <span className="muted num">{t.hand !== null ? `hand ${t.hand + 1}` : ''}
                  {t.blinds ? ` · ${fmt(t.blinds.sb)}/${fmt(t.blinds.bb)}` : ''}</span>
              </div>
              <div className="players">
                {t.players.map((x: any) => (
                  <div key={x.bot} className="prow">
                    <span className="muted num">{x.seat + 1}</span>
                    <span className="who">{x.bot}{t.button === x.seat && <span className="btn-d">D</span>}</span>
                    <span className={'num' + (leader && leader.bot === x.bot ? ' lead' : '')}>{fmt(x.stack)}</span>
                  </div>
                ))}
                {t.players.length === 0 && <div className="muted">empty</div>}
              </div>
              <button className="small" onClick={() => go(t.key)} data-testid="watch-table">
                {t.status === 'running' ? 'Watch' : 'Replay'}
              </button>
            </div>
          )
        })}
      </div>
      {closed.length > 0 && (
        <button className="ghost small" style={{ marginTop: 10 }} onClick={() => setShowClosed(v => !v)}>
          {showClosed ? 'Hide' : 'Show'} {closed.length} closed {closed.length === 1 ? 'table' : 'tables'}</button>
      )}
      {p.eliminations.length > 0 && (
        <section className="section" style={{ marginTop: 20 }}>
          <h2>Eliminations</h2>
          <div className="elims num">
            {p.eliminations.map((e: any) => (
              <span key={e.bot}><span className="muted">{ord(e.place)}</span> {e.bot}
                <span className="muted"> &middot; hand {e.hand + 1}, table {e.table}</span></span>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------

// Win rate per bot with its 95% interval: horizontal bars, single series.
export function SeriesChart({ rows, runs }: { rows: any[]; runs: number }) {
  const [hover, setHover] = useState<string | null>(null)
  const W = 640, ROW = 30, LABEL = 150, PAD_R = 70
  const H = rows.length * ROW + 24
  const x = (v: number) => LABEL + v * (W - LABEL - PAD_R)
  return (
    <div className="chart series-chart" data-testid="series-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Win rate by bot with 95% confidence intervals">
        <g className="grid axis">
          {[0, 0.25, 0.5, 0.75, 1].map(t => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={0} y2={H - 20} />
              <text x={x(t)} y={H - 6} textAnchor="middle" className="num">{t * 100}%</text>
            </g>
          ))}
        </g>
        {rows.map((r, i) => {
          const y = i * ROW + 6
          const [lo, hi] = r.win_rate_ci
          const on = hover === r.bot
          return (
            <g key={r.bot} onPointerEnter={() => setHover(r.bot)} onPointerLeave={() => setHover(null)}>
              <rect x={0} y={y - 4} width={W} height={ROW} fill="transparent" />
              <text x={LABEL - 10} y={y + 12} textAnchor="end" fill="var(--text-2)" fontSize="12">{r.bot}</text>
              <rect x={x(0)} y={y + 4} width={Math.max(x(r.win_rate) - x(0), 0)} height={12} rx={4}
                    fill="var(--p0)" opacity={hover && !on ? 0.5 : 1} />
              <line x1={x(lo)} x2={x(hi)} y1={y + 10} y2={y + 10} stroke="var(--text)" strokeWidth={1.5} />
              <line x1={x(lo)} x2={x(lo)} y1={y + 5} y2={y + 15} stroke="var(--text)" strokeWidth={1.5} />
              <line x1={x(hi)} x2={x(hi)} y1={y + 5} y2={y + 15} stroke="var(--text)" strokeWidth={1.5} />
              <text x={W - PAD_R + 8} y={y + 14} fill="var(--text)" fontSize="12" className="num">
                {r.wins}/{runs}
              </text>
            </g>
          )
        })}
      </svg>
      {hover && (() => {
        const r = rows.find(x => x.bot === hover)!
        return (
          <div className="tip num" style={{ right: 0, top: 0 }}>
            <div className="row"><span className="v" style={{ marginLeft: 0, paddingLeft: 0 }}>{(r.win_rate * 100).toFixed(1)}%</span>
              <span className="sub">{r.bot} win rate</span></div>
            <div className="row"><span className="sub">95% CI {(r.win_rate_ci[0] * 100).toFixed(1)}-{(r.win_rate_ci[1] * 100).toFixed(1)}%</span></div>
            <div className="row"><span className="sub">avg place {r.avg_place.toFixed(2)} &middot; top 3 {(r.top3_rate * 100).toFixed(0)}%</span></div>
          </div>
        )
      })()}
    </div>
  )
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

export function QualifierPanel({ p, active }: { p: any; active: boolean }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  // Count from the run's start time, not from each update's rounded "elapsed",
  // which made the clock jump when updates arrived. The offset between this
  // browser's clock and the server's is measured once and kept.
  const offset = useRef<number | null>(null)
  if (offset.current === null && typeof p.server_now === 'number') offset.current = p.server_now - Date.now() / 1000
  const budget = (p.time_budget_min ?? 0) * 60
  const elapsed = typeof p.started_at === 'number' && offset.current !== null
    ? Math.max(0, now / 1000 + offset.current - p.started_at) : null
  const times: number[] = p.cycle_seconds ?? []
  const typical = times.length ? times.reduce((a, b) => a + b, 0) / times.length : null
  const done = p.cycles_complete?.length ?? 0
  return (
    <div className="qualifier" data-testid="qualifier-panel">
      <div className="qual-stats">
        {active && elapsed != null && <div><div className="qual-big num">{clock(Math.max(0, budget - elapsed))}</div>
          <div className="muted">left of {p.time_budget_min} min</div></div>}
        <div><div className="qual-big num">{done}</div><div className="muted">complete cycle{done === 1 ? '' : 's'}</div></div>
        {active && p.running_cycle && <div><div className="qual-big num">{p.running_cycle_done}/{p.matches_per_cycle}</div>
          <div className="muted">matches in cycle {p.running_cycle}</div></div>}
        {typical != null && <div><div className="qual-big num">{clock(typical)}</div><div className="muted">per cycle</div></div>}
      </div>
      <p className="hint">Every cycle is a full duplicate round robin ({p.matches_per_cycle} matches). A cycle that has
        started always finishes, and a new one starts only if it can finish in the time left; only complete
        cycles count. The top {p.qualifiers} go through.</p>
    </div>
  )
}

// Player profiles (arena/profiles.py): how each bot plays, for commentary.
export function ProfilesTable({ profiles, order }: { profiles: any; order: string[] }) {
  // Click a heading: high to low, again: low to high, again: back to standings order.
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null)
  const bots: Record<string, any> = profiles?.bots ?? {}
  const standing = [...order.filter(n => bots[n]), ...Object.keys(bots).filter(n => !order.includes(n)).sort()]
  if (!standing.length) return null
  const value = (n: string, key: string) => key === 'bot' ? n : bots[n][key]
  const names = !sort ? standing : [...standing].sort((a, b) => {
    const x = value(a, sort.key), y = value(b, sort.key)
    if (x == null && y == null) return 0
    if (x == null) return 1                              // no sample: always last
    if (y == null) return -1
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y
    return c * sort.dir
  })
  const click = (key: string) => setSort(cur =>
    !cur || cur.key !== key ? { key, dir: key === 'bot' ? 1 : -1 }
      : cur.dir === (key === 'bot' ? 1 : -1) ? { key, dir: key === 'bot' ? -1 : 1 } : null)
  const arrow = (key: string) => sort?.key === key ? (sort.dir === -1 ? ' \u25BC' : ' \u25B2') : ''
  const head = (key: string, title: string, help: string, right = true) => (
    <th key={key} className={(right ? 'r ' : '') + 'sortable' + (sort?.key === key ? ' sorted' : '')}
        title={help} onClick={() => click(key)} aria-sort={sort?.key === key ? (sort.dir === -1 ? 'descending' : 'ascending') : 'none'}>
      {title}{arrow(key)}</th>
  )
  const show = showStat
  const pending = profiles.finished - profiles.matches
  return (
    <div data-testid="profiles">
      <table className="list num profiles">
        <thead><tr>
          {head('bot', 'Bot', 'Click to sort by name', false)}
          {head('hands', 'Hands', 'Hands played. Click to sort.')}
          {PROFILE_COLUMNS.map(c => head(c.key, c.title, c.help + ' Click to sort.'))}
        </tr></thead>
        <tbody>
          {names.map(n => (
            <tr key={n}><td>{n}</td><td className="r">{(bots[n].hands ?? 0).toLocaleString('en-US')}</td>
              {PROFILE_COLUMNS.map(c => <td key={c.key} className="r">{show(bots[n][c.key], c.unit)}</td>)}</tr>
          ))}
        </tbody>
      </table>
      <p className="hint">{pending > 0 ? `Counting: ${profiles.matches.toLocaleString('en-US')} of ${profiles.finished.toLocaleString('en-US')} finished matches so far. ` : ''}
        Click a heading to sort by it (again to reverse, a third time for standings order); hover for what it measures.</p>
    </div>
  )
}

// Side prizes (arena/sideprizes.py): one board per measured prize, every bot's
// best entry, best first, with a link that opens the match at that hand.
const SIDE_BOARDS: { key: string; title: string; blurb: string }[] = [
  { key: 'bluffer', title: 'Biggest bluffer', blurb: 'Chips won by bluffs per 1,000 hands played. A bluff: a bet or raise after the flop with under 40% equity against a random hand that gets a fold.' },
  { key: 'lowest_showdown', title: 'Lowest winning showdown', blurb: 'The weakest hand to win at least 1,000 chips at showdown, with a bet on the river.' },
  { key: 'seven_two', title: 'Most won off 7-2', blurb: 'Chips won on winning seven-two hands, per seven-two hand dealt. Losses with it don\u2019t count against a bot.' },
  { key: 'comeback', title: 'Biggest comeback', blurb: 'The smallest share of the chips a bot fell to in a match it went on to win.' },
]
const SUIT: Record<string, string> = { s: '\u2660', h: '\u2665', d: '\u2666', c: '\u2663' }
function Cards({ cards }: { cards?: string[] | null }) {
  if (!cards?.length) return <span className="muted">-</span>
  return <span className="mini-cards">{cards.map(c => (
    <span key={c} className={c[1] === 'h' || c[1] === 'd' ? 'red' : ''}>{c[0] === 'T' ? '10' : c[0]}{SUIT[c[1]] ?? c[1]}</span>))}</span>
}

export function SidePrizes({ prizes }: { prizes: any }) {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const boards = prizes?.boards ?? {}
  if (!SIDE_BOARDS.some(b => boards[b.key]?.length)) return null
  const n = (x: number) => (x ?? 0).toLocaleString('en-US')
  const pending = prizes.finished - prizes.matches
  return (
    <div className="side-prizes" data-testid="side-prizes">
      {SIDE_BOARDS.map(b => {
        const rows: any[] = boards[b.key] ?? []
        const shown = open[b.key] ? rows : rows.slice(0, 5)
        const comeback = b.key === 'comeback'
        if (b.key === 'bluffer') return (
          <div key={b.key} className="side-board" data-testid="board-bluffer">
            <h3>{b.title}</h3>
            <p className="hint" style={{ marginTop: 0 }}>{b.blurb} Ranked after {n(prizes.bluffer_min_hands ?? 500)} hands.</p>
            {rows.length === 0 ? <p className="muted">Nobody yet.</p> : (
              <table className="list num">
                <thead><tr><th>#</th><th>Bot</th><th className="r" title="Chips won by bluffs, per 1,000 hands played">Per 1,000 hands</th>
                  <th className="r">Bluffs</th><th className="r">Total won</th><th>Biggest bluff</th><th /></tr></thead>
                <tbody>
                  {shown.map(r => (
                    <tr key={r.bot} className={r.rank === 1 ? 'qualified' : ''}>
                      <td>{r.rank ?? <span className="muted" title="Not enough hands yet">-</span>}</td><td>{r.bot}</td>
                      <td className="r">{n(Math.round(r.per_1000))}</td><td className="r">{n(r.bluffs)}</td><td className="r">+{n(r.won)}</td>
                      <td>{r.best ? <>+{n(r.best.amount)} with <Cards cards={r.best.cards} /> <span className="muted">{r.best.made}, {Math.round((r.best.equity ?? 0) * 100)}% equity; {r.best.opponent} folded</span> <Cards cards={r.best.opponent_cards} /> <span className="muted">{r.best.opponent_made}</span></>
                        : <span className="muted">-</span>}</td>
                      <td className="r">{r.best && <a href={`#/match/${r.best.key}?hand=${r.best.hand + 1}`} title={`Open ${r.best.match_id} at hand ${r.best.hand + 1}`}>
                        <button className="primary small">Watch</button></a>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {rows.length > 5 && <button className="ghost small" onClick={() => setOpen(o => ({ ...o, [b.key]: !o[b.key] }))}>
              {open[b.key] ? 'Show the top 5' : `Show all ${rows.length}`}</button>}
          </div>
        )
        if (b.key === 'seven_two') return (
          <div key={b.key} className="side-board" data-testid="board-seven_two">
            <h3>{b.title}</h3>
            <p className="hint" style={{ marginTop: 0 }}>{b.blurb} Ranked after {prizes.seven_two_min ?? 30} seven-two hands.</p>
            {rows.length === 0 ? <p className="muted">Nobody yet.</p> : (
              <table className="list num">
                <thead><tr><th>#</th><th>Bot</th><th className="r" title="Chips won on winning 7-2 hands, divided by 7-2 hands dealt">Per 7-2 dealt</th>
                  <th className="r">Dealt</th><th className="r">Won with it</th><th className="r">Total won</th><th>Best hand</th><th /></tr></thead>
                <tbody>
                  {shown.map(r => (
                    <tr key={r.bot} className={r.rank === 1 ? 'qualified' : ''}>
                      <td>{r.rank ?? <span className="muted" title="Not enough seven-two hands yet">-</span>}</td><td>{r.bot}</td>
                      <td className="r">{r.per_dealt.toFixed(0)}</td><td className="r">{n(r.dealt)}</td>
                      <td className="r">{n(r.wins)}</td><td className="r">+{n(r.won)}</td>
                      <td>{r.best ? <><Cards cards={r.best.cards} /> <span className="muted">vs {r.best.opponent}</span> <Cards cards={r.best.opponent_cards} /> +{n(r.best.amount)}</> : <span className="muted">-</span>}</td>
                      <td className="r">{r.best && <a href={`#/match/${r.best.key}?hand=${r.best.hand + 1}`} title={`Open ${r.best.match_id} at hand ${r.best.hand + 1}`}>
                        <button className="primary small">Watch</button></a>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {rows.length > 5 && <button className="ghost small" onClick={() => setOpen(o => ({ ...o, [b.key]: !o[b.key] }))}>
              {open[b.key] ? 'Show the top 5' : `Show all ${rows.length}`}</button>}
          </div>
        )
        return (
          <div key={b.key} className="side-board" data-testid={`board-${b.key}`}>
            <h3>{b.title}</h3>
            <p className="hint" style={{ marginTop: 0 }}>{b.blurb}</p>
            {rows.length === 0 ? <p className="muted">Nobody yet.</p> : (
              <table className="list num">
                <thead>{comeback
                  ? <tr><th>#</th><th>Bot</th><th className="r">Low point</th><th>Against</th><th /></tr>
                  : <tr><th>#</th><th>Bot</th><th>Hand</th><th>Against</th><th>Board</th><th className="r">Won</th><th /></tr>}</thead>
                <tbody>
                  {shown.map(r => (
                    <tr key={r.bot} className={r.rank === 1 ? 'qualified' : ''}>
                      <td>{r.rank}</td><td>{r.bot}</td>
                      {comeback ? <>
                        <td className="r">{(r.share * 100).toFixed(1)}% <span className="muted">({n(r.chips)} of {n(r.total)}, hand {r.hand + 1})</span></td>
                        <td>{r.opponent}</td>
                      </> : <>
                        <td><Cards cards={r.cards} />{r.made && <span className="muted"> {r.made}</span>}</td>
                        <td>{r.opponent} <Cards cards={r.opponent_cards} />{r.opponent_made && <span className="muted"> {r.opponent_made}</span>}</td>
                        <td>{r.board?.length ? <Cards cards={r.board} /> : <span className="muted">{r.showdown ? '' : 'no flop'}</span>}
                          {!r.showdown && r.board?.length > 0 && <span className="muted"> (folded)</span>}</td>
                        <td className="r">+{n(r.amount)}</td>
                      </>}
                      <td className="r"><a href={`#/match/${r.key}?hand=${r.hand + 1}`} title={`Open ${r.match_id} at hand ${r.hand + 1}`}>
                        <button className="primary small">Watch</button></a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {rows.length > 5 && <button className="ghost small" onClick={() => setOpen(o => ({ ...o, [b.key]: !o[b.key] }))}>
              {open[b.key] ? 'Show the top 5' : `Show all ${rows.length}`}</button>}
          </div>
        )
      })}
      {pending > 0 && <p className="hint">Counting: {n(prizes.matches)} of {n(prizes.finished)} finished matches so far.</p>}
    </div>
  )
}

// Best showdowns (arena/sideprizes.py): the strongest hands shown down in the
// run, for the commentator. Not a prize.
export function BestShowdowns({ prizes }: { prizes: any }) {
  const [open, setOpen] = useState(false)
  const rows: any[] = prizes?.showdowns ?? []
  if (!rows.length) return null
  const n = (x: number) => (x ?? 0).toLocaleString('en-US')
  const shown = open ? rows : rows.slice(0, 10)
  return (
    <div className="side-prizes" data-testid="best-showdowns">
      <div className="side-board">
        <p className="hint" style={{ marginTop: 0 }}>The strongest hands shown down so far, best first. Equal hands are ordered by the hand they beat. Not a prize.</p>
        <table className="list num">
          <thead><tr><th>#</th><th>Hand</th><th>Bot</th><th>Against</th><th>Board</th><th className="r">Won</th><th /></tr></thead>
          <tbody>
            {shown.map(r => (
              <tr key={`${r.key}/${r.hand}`}>
                <td>{r.rank}</td>
                <td><strong>{r.made}</strong></td>
                <td>{r.bot} <Cards cards={r.cards} /></td>
                <td>{r.opponent} <Cards cards={r.opponent_cards} /> <span className="muted">{r.opponent_made}</span></td>
                <td><Cards cards={r.board} /></td>
                <td className="r">{r.split ? <span className="muted">split</span> : `+${n(r.amount)}`}</td>
                <td className="r"><a href={`#/match/${r.key}?hand=${r.hand + 1}`} title={`Open ${r.match_id} at hand ${r.hand + 1}`}>
                  <button className="primary small">Watch</button></a></td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length > 10 && <button className="ghost small" onClick={() => setOpen(o => !o)}>
          {open ? 'Show the top 10' : `Show the top ${rows.length}`}</button>}
      </div>
    </div>
  )
}
