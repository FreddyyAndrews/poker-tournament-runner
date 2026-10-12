import { useEffect, useState } from 'react'
import { api } from '../api'
import type { RunRow } from '../types'

const when = (t: number) => new Date(t * 1000).toLocaleString(undefined,
  { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

export function StatusPill({ status }: { status: string }) {
  if (status === 'running' || status === 'queued') return <span className="pill live"><span className="dot" />Live</span>
  if (status === 'complete') return <span className="pill">Complete</span>
  if (status === 'incomplete') return <span className="pill" title="Not running on this server and no final result">Didn't finish</span>
  if (status === 'cancelled') return <span className="pill">Stopped</span>
  if (status === 'error') return <span className="pill">Error</span>
  if (status === 'pending') return <span className="pill">Not started</span>
  return <span className="pill">{status}</span>
}

export function Home() {
  const [rows, setRows] = useState<RunRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = () => api.runs().then(r => alive && setRows(r)).catch(e => alive && setError(String(e)))
    load()
    const t = setInterval(load, 3000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  return (
    <div className="container">
      <h1>Recent</h1>
      <p className="sub" style={{ marginTop: 0 }}>Matches, tournaments and series in data/runs, newest first.</p>
      {error && <p className="error-text">{error}</p>}
      {rows && rows.length === 0 && <p className="empty">Nothing yet. Start one with <span className="mono">poker match shark aggressor --pace 600</span>.</p>}
      {rows && rows.length > 0 && (
        <table className="list" data-testid="runs">
          <thead>
            <tr><th>Name</th><th>Type</th><th>Players</th><th>Winner</th><th>Status</th><th className="r">Updated</th></tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="link" data-testid="run-row"
                  onClick={() => { location.hash = r.kind === 'match' ? `#/match/${r.id}` : `#/run/${r.id}` }}>
                <td><a href={r.kind === 'match' ? `#/match/${r.id}` : `#/run/${r.id}`}>{r.name || r.id}</a></td>
                <td className="sub">{r.kind === 'series' ? `${r.format} x${r.runs}` : r.kind === 'match' ? 'match' : r.format}</td>
                <td className="sub num">{r.bots.length}</td>
                <td>{r.winner ?? <span className="muted">-</span>}</td>
                <td><StatusPill status={r.status} /></td>
                <td className="r sub num">{when(r.modified)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
