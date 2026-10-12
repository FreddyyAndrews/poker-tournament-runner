import { useEffect, useMemo, useState } from 'react'
import { playerColor } from '../components/PokerTable'
import type { Level } from '../types'

type Format = 'table' | 'bracket' | 'round_robin' | 'mtt'
type Mode = 'fixed' | 'cash' | 'freezeout'
type BotInfo = { name: string; folder: string; path: string; hash: string }

const FORMATS: { id: Format; title: string; blurb: string }[] = [
  { id: 'table', title: 'Table', blurb: 'One table of 2-9 bots. Heads-up is a table of 2.' },
  { id: 'bracket', title: 'Bracket', blurb: 'Heads-up knockout. Byes go to the top seeds.' },
  { id: 'round_robin', title: 'Round robin', blurb: 'Every pair plays heads-up.' },
  { id: 'mtt', title: 'Multi-table', blurb: 'Freezeout across tables, down to a final table.' },
]

const MODES: { id: Mode; title: string; blurb: string }[] = [
  { id: 'freezeout', title: 'Freezeout', blurb: 'Until one bot has every chip; blinds rise' },
  { id: 'fixed', title: 'Fixed hands', blurb: 'A set number of hands; stacks carry over' },
  { id: 'cash', title: 'Cash', blurb: 'A set number of hands, each from the starting stack' },
]

const PACES = [
  { ms: 600, title: 'Live', blurb: 'about 0.6 s per action' },
  { ms: 1200, title: 'Slow', blurb: 'about 1.2 s per action' },
  { ms: 250, title: 'Quick', blurb: 'about 0.25 s per action' },
  { ms: 0, title: 'Instant', blurb: 'full speed; watch as a replay' },
]

const PRESETS = ['turbo', 'standard', 'slow']

const fmt = (n: number) => n.toLocaleString('en-US')

export function Launch() {
  const [bots, setBots] = useState<BotInfo[]>([])
  const [defaults, setDefaults] = useState<Record<Format, any> | null>(null)
  const [presets, setPresets] = useState<Record<string, { levels: Level[] }>>({})

  const [format, setFormat] = useState<Format>('table')
  const [entrants, setEntrants] = useState<string[]>([])
  const [mode, setMode] = useState<Mode>('freezeout')
  const [hands, setHands] = useState(100)
  const [stack, setStack] = useState(10_000)
  const [blindKind, setBlindKind] = useState<'preset' | 'flat'>('preset')
  const [preset, setPreset] = useState('standard')
  const [flat, setFlat] = useState({ sb: 50, bb: 100, ante: 0 })
  const [tableSize, setTableSize] = useState(6)
  const [duplicate, setDuplicate] = useState(true)
  const [seeding, setSeeding] = useState<'random' | 'given'>('random')
  const [runs, setRuns] = useState(1)
  const [workers, setWorkers] = useState(4)
  const [pace, setPace] = useState(600)
  const [seed, setSeed] = useState('')
  const [timeLimit, setTimeLimit] = useState('')
  const [docker, setDocker] = useState(false)
  const [humanSecs, setHumanSecs] = useState(30)
  const [llmSecs, setLlmSecs] = useState(120)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/bots').then(r => r.json()).then(setBots).catch(e => setError(String(e)))
    fetch('/api/structures').then(r => r.json()).then(d => setDefaults(d.formats))
  }, [])
  useEffect(() => {
    fetch(`/api/structures?stack=${stack || 10000}`).then(r => r.json()).then(d => setPresets(d.presets))
  }, [stack])

  // Format defaults (mode, hands, blinds, duplicate) when the format changes.
  useEffect(() => {
    if (!defaults) return
    const d = defaults[format]
    setMode(d.mode)
    setHands(d.mode === 'freezeout' ? 2000 : d.hands)
    if (PRESETS.includes(d.blinds)) { setBlindKind('preset'); setPreset(d.blinds) }
    else { setBlindKind('flat'); const [sb, bb, ante = 0] = d.blinds.split('/').map(Number); setFlat({ sb, bb, ante }) }
    setDuplicate(Boolean(d.duplicate) || format === 'round_robin')
  }, [format, defaults])

  useEffect(() => {
    if (mode === 'freezeout' && blindKind === 'flat') { setBlindKind('preset'); setPreset('standard') }
    if (mode !== 'freezeout' && hands > 1000) setHands(format === 'round_robin' ? 200 : 100)
  }, [mode])

  const series = runs > 1
  const remote = entrants.filter(e => e.startsWith('human:') || e.startsWith('mcp:'))
  const remoteOk = format === 'table' && !series
  const blinds = blindKind === 'preset' ? preset : `${flat.sb}/${flat.bb}${flat.ante ? '/' + flat.ante : ''}`
  const problem = useMemo(() => {
    const n = entrants.length
    if (n < 2) return 'Add at least two players.'
    if (remote.length && !remoteOk) return 'You and LLM seats can only play a single table, played once.'
    if (format === 'table' && n > 9) return 'A table seats at most 9; use Multi-table for more.'
    if (format === 'table' && duplicate && n !== 2) return 'Duplicate needs exactly two players.'
    if (format === 'mtt' && mode !== 'freezeout') return 'Multi-table is always a freezeout.'
    if (blindKind === 'flat' && !(flat.sb > 0 && flat.sb <= flat.bb)) return 'Small blind must be between 1 and the big blind.'
    if (stack < 1) return 'Starting stack must be positive.'
    if (hands < 1) return 'Hands must be at least 1.'
    return null
  }, [entrants, format, duplicate, mode, blindKind, flat, stack, hands, remote.length, remoteOk])

  const summary = useMemo(() => {
    const n = entrants.length
    const what = format === 'table' ? (n === 2 ? 'Heads-up match' : `${n || 'N'}-player table`)
      : format === 'bracket' ? `${n || 'N'}-bot knockout bracket`
      : format === 'round_robin' ? `${n || 'N'}-bot round robin`
      : `${n || 'N'}-bot multi-table tournament (${tableSize} per table)`
    const how = mode === 'freezeout' ? `freezeout at ${blindKind === 'preset' ? preset : blinds} blinds`
      : `${fmt(hands)} ${mode === 'cash' ? 'cash ' : ''}hands at ${blinds}`
    const dup = duplicate && (format === 'round_robin' || (format === 'table' && n === 2)) ? ', duplicate' : ''
    const watch = series ? `played ${runs} times at full speed`
      : pace === 600 ? 'watched live' : pace ? `watched live at a ${PACES.find(p => p.ms === pace)?.title.toLowerCase()} pace` : 'at full speed'
    return `${what}: ${how}${dup}, ${fmt(stack)} chips each, ${watch}.`
  }, [entrants, format, mode, blindKind, preset, blinds, hands, duplicate, series, runs, pace, stack, tableSize])

  // Rough wall-clock estimate for paced play (about 7 paced steps per hand).
  const estimate = useMemo(() => {
    const n = entrants.length
    if (series || !pace || n < 2) return null
    const legs = (format === 'round_robin' && duplicate) || (format === 'table' && n === 2 && duplicate) ? 2 : 1
    const freeze = (players: number) => Math.min(hands, 60 + 40 * players)
    const perMatch = mode === 'freezeout' ? freeze(format === 'table' ? n : format === 'mtt' ? Math.min(n, tableSize) : 2) : hands
    const matches = format === 'table' ? legs : format === 'bracket' ? n - 1
      : format === 'round_robin' ? (n * (n - 1) / 2) * legs : 1
    const totalHands = format === 'mtt' ? freeze(n) : perMatch * matches
    const minutes = (totalHands * 7 * pace) / 60000
    return minutes < 2 ? 'about a minute' : minutes < 90 ? `roughly ${Math.round(minutes)} minutes` : `roughly ${(minutes / 60).toFixed(1)} hours`
  }, [entrants, series, pace, format, duplicate, mode, hands, tableSize])

  async function launch() {
    if (problem) return
    setBusy(true); setError(null)
    const common = {
      bots: entrants, mode, hands, starting_stack: stack, blinds,
      seed: seed.trim() ? Number(seed) : undefined,
      time_limit_ms: timeLimit.trim() ? Number(timeLimit) : undefined,
      docker, pace_ms: series ? 0 : pace,
    }
    try {
      if (format === 'table' && !series) {
        const r = await post('/api/matches', { ...common, names: undefined,
          time_limits: { human: humanSecs * 1000, mcp: llmSecs * 1000 } })
        const me = (r.seats ?? []).find((x: any) => x.kind === 'human')
        location.hash = me
          ? `#/match/${r.match_key}?seat=${encodeURIComponent(me.token)}&as=${encodeURIComponent(me.name)}`
          : `#/match/${r.match_key}`
      } else {
        const r = await post('/api/runs', {
          ...common, format, name: name.trim() || undefined, runs, workers: series ? workers : (pace ? 1 : workers),
          table_size: format === 'mtt' ? tableSize : undefined,
          duplicate: format === 'round_robin' || format === 'table' ? duplicate : undefined,
          seeding: format === 'bracket' ? seeding : undefined,
        })
        location.hash = `#/run/${r.run_id}`
      }
    } catch (e) {
      setError(String(e).replace(/^Error: /, ''))
    } finally {
      setBusy(false)
    }
  }

  const levels = presets[preset]?.levels ?? []
  const add = (b: string) => setEntrants(es => [...es, b])
  const remove = (i: number) => setEntrants(es => es.filter((_, j) => j !== i))

  return (
    <div className="container launch" data-testid="launch">
      <h1>New</h1>
      <p className="sub" style={{ marginTop: 0 }}>Start a match, tournament or series. Everything is saved under data/runs.</p>

      <div className="launch-grid">
        <div>
          <section className="section">
            <h2>Format</h2>
            <div className="choice-grid">
              {FORMATS.map(f => (
                <button key={f.id} className={'choice' + (format === f.id ? ' on' : '')} onClick={() => setFormat(f.id)}
                        data-testid={`format-${f.id}`}>
                  <span className="t">{f.title}</span><span className="b">{f.blurb}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="section">
            <h2>Players {entrants.length > 0 && <span className="muted">({entrants.length})</span>}</h2>
            <div className="entrants" data-testid="entrants">
              {entrants.length === 0 && <span className="muted">Add bots from the list below.</span>}
              {entrants.map((b, i) => (
                <span key={i} className="chip">
                  {format === 'bracket' && seeding === 'given' && <span className="muted num">{i + 1}</span>}
                  <span className="swatch" style={{ background: playerColor(i) }} />{label(b)}
                  <button className="x" onClick={() => remove(i)} aria-label={`Remove ${b}`}>&times;</button>
                </span>
              ))}
              {entrants.length > 0 && <button className="ghost small" onClick={() => setEntrants([])}>Clear</button>}
            </div>
            <div className="bot-list">
              <div className="bot-row">
                <span>You <span className="muted">(play from this browser)</span></span>
                <span className="muted mono">human</span><span />
                <button className="small" disabled={!remoteOk || entrants.some(e => e.startsWith('human:'))}
                        title={remoteOk ? '' : 'Only for a single table, played once'}
                        onClick={() => add('human:You')} data-testid="add-human">Add</button>
              </div>
              <div className="bot-row">
                <span>An LLM <span className="muted">(over MCP, e.g. Claude)</span></span>
                <span className="muted mono">mcp</span><span />
                <button className="small" disabled={!remoteOk} title={remoteOk ? '' : 'Only for a single table, played once'}
                        onClick={() => add(`mcp:${entrants.some(e => e.startsWith('mcp:')) ? 'llm' + (remote.length + 1) : 'claude'}`)}
                        data-testid="add-mcp">Add</button>
              </div>
              {bots.map(b => (
                <div key={b.path} className="bot-row">
                  <span>{b.name}</span>
                  <span className="muted mono">{b.folder}/</span>
                  <span className="muted mono" title="code version">{b.hash.slice(0, 8)}</span>
                  <button className="small" onClick={() => add(b.name)} data-testid={`add-${b.name}`}>Add</button>
                </div>
              ))}
            </div>
            <button className="ghost small" style={{ marginTop: 8 }}
                    onClick={() => setEntrants(es => [...es, ...bots.map(b => b.name)])}>Add every bot</button>
          </section>

          <section className="section">
            <h2>How each match is played</h2>
            <div className="seg" role="group">
              {MODES.map(m => (
                <button key={m.id} className={mode === m.id ? 'on' : ''} onClick={() => setMode(m.id)}
                        disabled={format === 'mtt' && m.id !== 'freezeout'} title={m.blurb}
                        data-testid={`mode-${m.id}`}>{m.title}</button>
              ))}
            </div>
            <p className="hint">{MODES.find(m => m.id === mode)?.blurb}</p>
            <div className="fields">
              <label>{mode === 'freezeout' ? 'Hand cap' : 'Hands'}
                <input type="number" min={1} value={hands} onChange={e => setHands(Number(e.target.value))} data-testid="hands" /></label>
              <label>Starting stack
                <input type="number" min={1} step={500} value={stack} onChange={e => setStack(Number(e.target.value))} data-testid="stack" /></label>
              {format === 'mtt' && <label>Players per table
                <input type="number" min={2} max={9} value={tableSize} onChange={e => setTableSize(Number(e.target.value))} /></label>}
            </div>
            {(format === 'round_robin' || (format === 'table' && entrants.length === 2)) && (
              <label className="check"><input type="checkbox" checked={duplicate} onChange={e => setDuplicate(e.target.checked)} />
                Duplicate: replay every deal with the seats swapped (cancels most card luck)</label>
            )}
            {format === 'bracket' && (
              <label className="check"><input type="checkbox" checked={seeding === 'given'}
                     onChange={e => setSeeding(e.target.checked ? 'given' : 'random')} />
                Seed in the order players were added (otherwise random)</label>
            )}
          </section>

          <section className="section">
            <h2>Blinds</h2>
            <div className="seg" role="group">
              {PRESETS.map(p => (
                <button key={p} className={blindKind === 'preset' && preset === p ? 'on' : ''}
                        onClick={() => { setBlindKind('preset'); setPreset(p) }} data-testid={`blinds-${p}`}>
                  {p[0].toUpperCase() + p.slice(1)}</button>
              ))}
              <button className={blindKind === 'flat' ? 'on' : ''} disabled={mode === 'freezeout'}
                      onClick={() => setBlindKind('flat')} title={mode === 'freezeout' ? 'Freezeouts need rising blinds' : ''}
                      data-testid="blinds-flat">Flat</button>
            </div>
            {blindKind === 'flat' ? (
              <div className="fields" style={{ marginTop: 10 }}>
                <label>Small blind<input type="number" min={1} value={flat.sb} onChange={e => setFlat({ ...flat, sb: Number(e.target.value) })} /></label>
                <label>Big blind<input type="number" min={1} value={flat.bb} onChange={e => setFlat({ ...flat, bb: Number(e.target.value) })} /></label>
                <label>Ante<input type="number" min={0} value={flat.ante} onChange={e => setFlat({ ...flat, ante: Number(e.target.value) })} /></label>
              </div>
            ) : (
              <>
                <p className="hint">Levels rise every {levels[0]?.hands ?? '-'} hands, scaled to the starting stack.</p>
                <div className="levels num">
                  {levels.slice(0, 10).map((l, i) => (
                    <span key={i}><span className="muted">{i + 1}</span> {fmt(l.sb)}/{fmt(l.bb)}{l.ante ? ` a${fmt(l.ante)}` : ''}</span>
                  ))}
                  <span className="muted">...</span>
                </div>
              </>
            )}
          </section>

          <section className="section">
            <h2>Watching and repeats</h2>
            <div className="fields">
              <label>Play it
                <select value={runs > 1 ? 'series' : 'once'} onChange={e => setRuns(e.target.value === 'series' ? 100 : 1)} data-testid="repeat">
                  <option value="once">Once</option>
                  <option value="series">Many times (a series)</option>
                </select></label>
              {series && <label>Runs<input type="number" min={2} value={runs} onChange={e => setRuns(Math.max(2, Number(e.target.value)))} data-testid="runs" /></label>}
              {series && <label>In parallel<input type="number" min={1} max={32} value={workers} onChange={e => setWorkers(Number(e.target.value))} /></label>}
            </div>
            {!series && (
              <div className="seg" role="group" style={{ marginTop: 10 }}>
                {PACES.map(p => (
                  <button key={p.ms} className={pace === p.ms ? 'on' : ''} onClick={() => setPace(p.ms)} title={p.blurb}
                          data-testid={`pace-${p.ms}`}>{p.title}</button>
                ))}
              </div>
            )}
            <p className="hint">{series ? 'A series always runs at full speed; any single match can be replayed afterwards.'
              : PACES.find(p => p.ms === pace)?.blurb}</p>
          </section>

          <details className="section">
            <summary className="muted" style={{ cursor: 'pointer' }}>Advanced</summary>
            <div className="fields" style={{ marginTop: 10 }}>
              {format !== 'table' && <label>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="optional" /></label>}
              <label>Seed<input value={seed} onChange={e => setSeed(e.target.value.replace(/\D/g, ''))} placeholder="random" data-testid="seed" /></label>
              <label>Bot time limit (ms)<input value={timeLimit} onChange={e => setTimeLimit(e.target.value.replace(/\D/g, ''))} placeholder="2000" /></label>
              <label>Your time (s)<input type="number" min={5} value={humanSecs} onChange={e => setHumanSecs(Number(e.target.value))} /></label>
              <label>LLM time (s)<input type="number" min={5} value={llmSecs} onChange={e => setLlmSecs(Number(e.target.value))} /></label>
            </div>
            <label className="check"><input type="checkbox" checked={docker} onChange={e => setDocker(e.target.checked)} />
              Run bots in the Docker sandbox (build it first: ./sandbox.sh build)</label>
          </details>
        </div>

        <aside className="launch-side">
          <div className="summary" data-testid="summary">{summary}</div>
          {estimate && <p className="hint" style={{ marginTop: 0 }}>At this pace: {estimate}.</p>}
          {problem && <p className="hint">{problem}</p>}
          {error && <p className="error-text" data-testid="launch-error">{error}</p>}
          <button className="primary" disabled={!!problem || busy} onClick={launch} data-testid="launch-btn">
            {busy ? 'Starting...' : series ? `Run ${runs} times` : entrants.some(e => e.startsWith('human:')) ? 'Start and play'
              : pace ? 'Start and watch' : 'Start'}
          </button>
        </aside>
      </div>
    </div>
  )
}

function label(entrant: string) {
  if (entrant.startsWith('human:')) return `${entrant.slice(6)} (you)`
  if (entrant.startsWith('mcp:')) return `${entrant.slice(4)} (LLM)`
  return entrant
}

async function post(path: string, body: unknown) {
  const r = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d: any) => d.msg).join('; ') : data.detail ?? r.status)
  return data
}

