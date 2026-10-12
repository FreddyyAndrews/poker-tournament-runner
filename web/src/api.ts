import type { MatchMeta, PokerEvent, RunRow } from './types'

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`)
  return r.json() as Promise<T>
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail ? String(data.detail) : `${r.status}`)
  return data as T
}

// Match keys contain slashes (run/matches/id); keep them, encode the parts.
const k = (key: string) => key.split('/').map(encodeURIComponent).join('/')

export const api = {
  runs: () => get<RunRow[]>('/api/runs'),
  run: (id: string) => get<any>(`/api/runs/${encodeURIComponent(id)}`),
  match: (key: string) => get<MatchMeta>(`/api/matches/${k(key)}`),
  events: (key: string, from = 0) =>
    get<{ events: PokerEvent[]; next: number; status: string }>(`/api/matches/${k(key)}/events?from=${from}`),
  streamUrl: (key: string, from: number) => `/api/matches/${k(key)}/stream?from=${from}`,
  exportUrl: (key: string, bot?: string) => `/api/export/${k(key)}${bot ? `?bot=${encodeURIComponent(bot)}` : ''}`,
  cancel: (jobId: string) => post(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {}),
}
