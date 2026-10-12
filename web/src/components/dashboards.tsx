// Format-specific run dashboards: bracket tree, round-robin grid, multi-table
// overview, series results. Each takes the server's `progress` object.

import { useState } from 'react'
import { ord } from './PokerTable'

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
                      <span className="num">{t.stacks && name ? fmt(t.stacks[name]) : ''}</span>
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
                {!t.closed && !t.ended && <Live />}
                {t.closed && <span className="pill">Closed</span>}
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
                {t.closed || t.ended ? 'Replay' : 'Watch'}
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
