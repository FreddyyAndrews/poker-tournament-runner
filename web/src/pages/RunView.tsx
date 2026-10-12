import { useEffect, useState } from 'react'
import { api } from '../api'
import { StatusPill } from './Home'

const fmt = (n: number) => (n ?? 0).toLocaleString('en-US')

// Basic run / series page. Format-specific dashboards come later.
export function RunView({ runId }: { runId: string }) {
  const [run, setRun] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true, timer: number | undefined
    const load = () => api.run(runId).then(r => {
      if (!alive) return
      setRun(r)
      const active = r.job && ['queued', 'running'].includes(r.job.status)
      if (active) timer = window.setTimeout(load, 1500)
    }).catch(e => alive && setError(String(e)))
    load()
    return () => { alive = false; clearTimeout(timer) }
  }, [runId])

  if (error) return <div className="container"><h1>Not found</h1><p className="muted">{error}</p></div>
  if (!run) return <div className="container"><p className="muted">Loading...</p></div>
  const d = run.data ?? {}
  const status = run.job?.status ?? d.status ?? 'complete'
  const live = new Set<string>(run.job?.live ?? [])

  return (
    <div className="container" data-testid="run-view">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
        <h1 style={{ margin: 0 }}>{d.name || runId}</h1>
        <StatusPill status={status} />
      </div>
      <p className="sub" style={{ marginTop: 4 }}>
        {run.kind === 'series' ? `${d.runs ?? run.job?.params?.runs} x ${d.format ?? run.job?.params?.format}` : d.format}
        {d.seed !== undefined && <span className="muted"> &middot; seed {d.seed}</span>}
        {run.kind === 'series' && run.job?.status === 'running' && <> &middot; {run.job.runs_done} runs done</>}
      </p>

      {run.kind === 'series' && d.aggregate && (
        <section className="section">
          <h2>Results across runs</h2>
          <table className="list num">
            <thead><tr><th>Bot</th><th className="r">Wins</th><th className="r">Win rate</th><th className="r">95% CI</th>
              <th className="r">Avg place</th><th className="r">bb/100</th></tr></thead>
            <tbody>
              {d.aggregate.map((a: any) => (
                <tr key={a.bot}><td>{a.bot}</td><td className="r">{a.wins}</td>
                  <td className="r">{(a.win_rate * 100).toFixed(1)}%</td>
                  <td className="r sub">{(a.win_rate_ci[0] * 100).toFixed(1)}-{(a.win_rate_ci[1] * 100).toFixed(1)}%</td>
                  <td className="r">{a.avg_place.toFixed(2)}</td><td className="r">{a.bb_per_100.toFixed(1)}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {run.kind === 'series' && d.run_ids && (
        <section className="section">
          <h2>Runs</h2>
          <table className="list">
            <tbody>
              {d.run_ids.map((id: string, i: number) => (
                <tr key={id} className="link" onClick={() => { location.hash = `#/run/${runId}/${id}` }}>
                  <td>{id}</td><td className="sub">winner {d.winners[i]}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {run.kind === 'run' && d.standings && (
        <section className="section">
          <h2>Standings</h2>
          <table className="list num">
            <thead><tr><th>#</th><th>Bot</th><th className="r">Chips</th><th className="r">bb/100</th><th className="r">Hands</th></tr></thead>
            <tbody>
              {d.standings.map((s: any) => (
                <tr key={s.bot}><td>{s.place}</td><td>{s.bot}</td><td className="r">{s.delta > 0 ? '+' : ''}{fmt(s.delta)}</td>
                  <td className="r">{(s.bb_per_100 ?? 0).toFixed(1)}</td><td className="r">{fmt(s.hands_played)}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {run.kind === 'run' && run.matches && (
        <section className="section">
          <h2>Matches</h2>
          {run.matches.length === 0 && <p className="empty">No matches started yet.</p>}
          <table className="list" data-testid="run-matches">
            <tbody>
              {run.matches.map((m: any) => (
                <tr key={m.key} className="link" onClick={() => { location.hash = `#/match/${m.key}` }}>
                  <td>{m.match_id}</td>
                  <td><StatusPill status={live.has(m.key) ? 'running' : m.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}
