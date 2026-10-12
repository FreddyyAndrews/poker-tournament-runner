// Remember which seat you hold in each match, so leaving the page and coming
// back (e.g. via Recent) puts you back in your seat. Browser storage can be
// unavailable (private windows, blocked storage): every access is guarded and
// the server-side "Play as" offer covers that case.

const KEY = 'poker.seats'

type Saved = Record<string, { token: string; name: string; at: number }>

function load(): Saved {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') as Saved } catch { return {} }
}

function save(s: Saved) {
  try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* storage unavailable */ }
}

export function rememberSeat(matchKey: string, token: string, name: string) {
  const s = load()
  s[matchKey] = { token, name, at: Date.now() }
  // keep the most recent 50
  const keys = Object.keys(s).sort((a, b) => s[b].at - s[a].at)
  for (const k of keys.slice(50)) delete s[k]
  save(s)
}

export function rememberedSeat(matchKey: string) {
  return load()[matchKey] ?? null
}

export function forgetSeat(matchKey: string) {
  const s = load()
  delete s[matchKey]
  save(s)
}

export function seatHash(matchKey: string, token: string, name: string) {
  return `#/match/${matchKey}?seat=${encodeURIComponent(token)}&as=${encodeURIComponent(name)}`
}
