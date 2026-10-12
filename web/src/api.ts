import type { BotState, MatchMeta, PokerEvent, RunRow } from './types'

export type SeatView = {
  name: string; kind: string; match_key: string; time_limit_ms: number; finished: boolean
  pending: { id: number; state: BotState; time_left_ms: number; time_limit_ms: number } | null
}

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
  events: (key: string, from = 0, as?: string) =>
    get<{ events: PokerEvent[]; next: number; status: string }>(
      `/api/matches/${k(key)}/events?from=${from}${as ? `&as=${encodeURIComponent(as)}` : ''}`),
  streamUrl: (key: string, from: number, as?: string) =>
    `/api/matches/${k(key)}/stream?from=${from}${as ? `&as=${encodeURIComponent(as)}` : ''}`,
  seat: (token: string) => get<SeatView>(`/api/seats/${encodeURIComponent(token)}`),
  act: (token: string, body: { action: string; amount?: number; decision_id?: number }) =>
    post(`/api/seats/${encodeURIComponent(token)}/act`, body),
  turn: (key: string) => get<{ bot: string; kind: string; time_left_ms: number; time_limit_ms: number }[]>(
    `/api/matches/${k(key)}/turn`),
  exportUrl: (key: string, bot?: string) => `/api/export/${k(key)}${bot ? `?bot=${encodeURIComponent(bot)}` : ''}`,
  cancel: (jobId: string) => post(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {}),
}
