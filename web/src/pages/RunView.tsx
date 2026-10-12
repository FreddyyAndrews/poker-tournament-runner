import { useEffect, useState } from 'react'
import { api } from '../api'
import { BracketView, MttView, RoundRobinView, SeriesChart } from '../components/dashboards'
import { ord } from '../components/PokerTable'
import { StatusPill } from './Home'

const fmt = (n: number) => (n ?? 0).toLocaleString('en-US')
const FORMAT_NAME: Record<string, string> = {
  table: 'Table', bracket: 'Bracket', round_robin: 'Round robin', mtt: 'Multi-table',
}

export function RunView({ runId }: { runId: string }) {
  const [run, setRun] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [verify, setVerify] = useState<any>(null)

  useEffect(() => {
    let alive = true, timer: number | undefined
    const load = () => api.run(runId).then(r => {
      if (!alive) return
      setRun(r)
      const active = r.status === 'running'
      if (active) timer = window.setTimeout(load, 1500)
    }).catch(e => alive && setError(String(e)))
    load()
    return () => { alive = false; clearTimeout(timer) }
  }, [runId])

  if (error) return <div className="container"><h1>Not found</h1><p className="muted">{error}</p></div>
  if (!run) return <div className="container"><p className="muted">Loading...</p></div>

  const d = run.data ?? {}
  const p = run.progress
  // One rule, decided by the server: 'running' only while its job is running.
  const status: string = run.status ?? 'incomplete'
  const active = status === 'running'
  const format = d.format ?? run.job?.params?.format
  const cfg = d.config ?? {}
  const live: string[] = active ? (run.job?.live ?? []).filter(() => format !== 'mtt') : []
  const isSeries = run.kind === 'series'
  const runsTotal = d.runs ?? run.job?.params?.runs
  const runsDone = p?.runs_done ?? run.job?.runs_done ?? 0

  const doVerify = async () => {
    setVerify({ busy: true })
    const r = await fetch(`/api/verify/${runId}`)
    setVerify(await r.json())
  }

  return (
    <div className="container wide" data-testid="run-view">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ margin: 0 }}>{d.name || runId.split('/').pop()}</h1>
        <StatusPill status={status} />
        <span className="spacer" style={{ flex: 1 }} />
        {active && run.job && <button className="ghost" onClick={() => api.cancel(run.job.id)}>Stop</button>}
        {!active && <button className="ghost" onClick={doVerify} data-testid="verify">Verify</button>}
        {!active && <a href={api.exportUrl(runId)}><button className="ghost">Download</button></a>}
      </div>
      <p className="sub" style={{ marginTop: 6 }}>
        {isSeries ? `${FORMAT_NAME[format] ?? format}, played ${runsTotal} times` : FORMAT_NAME[format] ?? format}
        {cfg.mode && <> &middot; {cfg.mode === 'freezeout' ? 'freezeout' : `${cfg.hands} ${cfg.mode} hands`}</>}
        {cfg.blinds && <> &middot; {cfg.blinds} blinds</>}
        {cfg.starting_stack && <> &middot; {fmt(cfg.starting_stack)} chips</>}
        {d.seed !== undefined && <span className="muted"> &middot; seed {d.seed}</span>}
      </p>
      {verify && (
        <p className={verify.ok ? 'sub' : 'error-text'} data-testid="verify-result">
          {verify.busy ? 'Replaying every match log...' : verify.ok
            ? 'Verified: every match log replays exactly and the standings match.'
            : `Verification failed: ${(verify.problems ?? []).slice(0, 3).join('; ')}`}
        </p>
      )}

      {isSeries && (
        <>
          <div className="progress" title={`${runsDone} of ${runsTotal} runs`}>
            <span style={{ width: `${runsTotal ? (100 * runsDone) / runsTotal : 0}%` }} />
          </div>
          <section className="section">
            <h2>Win rate across {runsDone} {runsDone === 1 ? 'run' : 'runs'}{active ? ' so far' : ''}</h2>
            {p?.aggregate?.length ? <SeriesChart rows={p.aggregate} runs={runsDone} />
              : <p className="empty">Waiting for the first run to finish.</p>}
          </section>
          {p?.aggregate?.length > 0 && (
            <section className="section">
              <table className="list num" data-testid="series-table">
                <thead><tr><th>Bot</th><th className="r">Wins</th><th className="r">Win rate</th><th className="r">95% CI</th>
                  <th className="r">Top 3</th><th className="r">Avg place</th><th className="r">bb/100</th><th>Places</th></tr></thead>
                <tbody>
                  {p.aggregate.map((a: any) => (
                    <tr key={a.bot}><td>{a.bot}</td><td className="r">{a.wins}</td>
                      <td className="r">{(a.win_rate * 100).toFixed(1)}%</td>
                      <td className="r sub">{(a.win_rate_ci[0] * 100).toFixed(1)}-{(a.win_rate_ci[1] * 100).toFixed(1)}%</td>
                      <td className="r">{(a.top3_rate * 100).toFixed(0)}%</td>
                      <td className="r">{a.avg_place.toFixed(2)}</td><td className="r">{a.bb_per_100.toFixed(1)}</td>
                      <td className="sub">{Object.entries(a.place_counts).map(([pl, c]) => `${ord(Number(pl))} ${c}`).join(' \u00b7 ')}</td></tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
          {p?.winners?.length > 0 && (
            <details className="section">
              <summary className="muted" style={{ cursor: 'pointer' }}>Every run ({p.winners.length})</summary>
              <table className="list" style={{ marginTop: 8 }}>
                <tbody>
                  {p.winners.map((w: any) => (
                    <tr key={w.run_id} className="link" onClick={() => { location.hash = `#/run/${runId}/${w.run_id}` }}>
                      <td>{w.run_id}</td><td className="sub">won by {w.winner}</td></tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}

      {live.length > 0 && (
        <section className="section now-playing" data-testid="now-playing">
          <h2>Now playing</h2>
          {live.map(k => (
            <div key={k} className="np-row">
              <span className="pill live"><span className="dot" />Live</span>
              <span>{k.split('/').pop()}</span>
              <span className="spacer" />
              <a href={`#/match/${k}`}><button className="primary small" data-testid="watch">Watch</button></a>
            </div>
          ))}
        </section>
      )}

      {p?.kind === 'bracket' && <section className="section"><BracketView p={p} /></section>}
      {p?.kind === 'round_robin' && <section className="section"><h2>Head to head</h2><RoundRobinView p={p} /></section>}
      {p?.kind === 'mtt' && <section className="section"><MttView p={p} /></section>}

      {!isSeries && (d.standings || p?.standings?.length > 0) && (
        <section className="section">
          <h2>{d.standings ? 'Final standings' : 'Standings so far'}</h2>
          <table className="list num" data-testid="standings-table">
            <thead><tr><th>#</th><th>Bot</th>{format === 'round_robin' && <th className="r">Points</th>}
              {(format === 'bracket' || format === 'round_robin') && <th className="r">W-L</th>}
              <th className="r">Chips won</th><th className="r">bb/100</th><th className="r">Hands</th>
              <th className="r">Timeouts</th><th className="r">Errors</th></tr></thead>
            <tbody>
              {(d.standings ?? p.standings).map((s: any) => (
                <tr key={s.bot}><td>{s.place}</td><td>{s.bot}</td>
                  {format === 'round_robin' && <td className="r">{s.points}</td>}
                  {(format === 'bracket' || format === 'round_robin') &&
                    <td className="r">{s.wins}-{s.losses}{s.draws ? `-${s.draws}` : ''}</td>}
                  <td className="r">{s.delta > 0 ? '+' : ''}{fmt(s.delta)}</td>
                  <td className="r">{(s.bb_per_100 ?? 0).toFixed(1)}</td><td className="r">{fmt(s.hands_played)}</td>
                  <td className="r">{s.timeouts ?? 0}</td><td className="r">{s.errors ?? 0}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {run.kind === 'run' && run.matches && format !== 'mtt' && (
        <details className="section" open={format === 'table'}>
          <summary className="muted" style={{ cursor: 'pointer' }}>All matches ({run.matches.length})</summary>
          <table className="list" data-testid="run-matches" style={{ marginTop: 8 }}>
            <tbody>
              {run.matches.map((m: any) => (
                <tr key={m.key} className="link" onClick={() => { location.hash = `#/match/${m.key}` }}>
                  <td>{m.match_id}</td><td><StatusPill status={m.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}
