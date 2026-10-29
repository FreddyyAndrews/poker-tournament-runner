import { useEffect, useMemo, useState } from 'react'
import { playerColor } from '../components/PokerTable'
import { rememberSeat, seatHash } from '../seatMemory'
import type { Level } from '../types'

// Where a bot lives: "bots/", "my_bots/", or another worktree's branch.
const folderLabel = (f: string) => f.startsWith('worktree:') ? f.slice(9) : f + '/'
const folderTitle = (f: string) => f.startsWith('worktree:') ? `my_bots/ in the git worktree on branch ${f.slice(9)}` : f + '/'


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

// Bots played through their own APIs (GTO Wizard, Slumbot): their game is fixed; see /api/external.
type ExternalBot = {
  name: string; title: string; configured: boolean; why: string | null; score_kind: string
  default_workers: number; max_workers: number; starting_stack: number; sb: number; bb: number
}
const EXTERNAL_BLURB: Record<string, string> = {
  gto_wizard: 'GTO Wizard AI, through its benchmark API; needs an API key',
  slumbot: 'Slumbot, through its free public API at slumbot.com',
}
const EXTERNAL_TITLE: Record<string, string> = { gto_wizard: 'GTO Wizard', slumbot: 'Slumbot' }

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
  const [maxParallel, setMaxParallel] = useState(false)    // as many matches at once as the tournament allows
  const [cpus, setCpus] = useState<number | null>(null)
  const [pace, setPace] = useState(600)
  const [seed, setSeed] = useState('')
  const [timeLimit, setTimeLimit] = useState('')
  const [docker, setDocker] = useState(false)
  const [humanSecs, setHumanSecs] = useState(30)
  const [llmSecs, setLlmSecs] = useState(120)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [externals, setExternals] = useState<ExternalBot[]>([])

  useEffect(() => {
    fetch('/api/bots').then(r => r.json()).then(setBots).catch(e => setError(String(e)))
    fetch('/api/structures').then(r => r.json()).then(d => setDefaults(d.formats))
    fetch('/api/external').then(r => r.json()).then(setExternals).catch(() => setExternals([]))
    fetch('/api/health').then(r => r.json()).then(h => setCpus(h.cpus ?? null)).catch(() => {})
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
  const isExternal = (e: string) => e in EXTERNAL_TITLE
  const ext = externals.find(x => entrants.includes(x.name)) ?? null
  const vsExt = entrants.some(isExternal)
  const g = ext ?? { name: '', title: 'it', score_kind: 'luck-adjusted', default_workers: 1, starting_stack: 20_000, sb: 50, bb: 100 }
  const blinds = blindKind === 'preset' ? preset : `${flat.sb}/${flat.bb}${flat.ante ? '/' + flat.ante : ''}`
  const problem = useMemo(() => {
    const n = entrants.length
    if (vsExt) {
      if (entrants.filter(isExternal).length > 1) return 'Play GTO Wizard or Slumbot, one at a time, against one of your players.'
      if (!remoteOk) return `${g.title} plays a single heads-up match, played once.`
      if (n !== 2) return `${g.title} plays heads-up: add exactly one other player.`
      if (hands < 1) return 'Hands must be at least 1.'
      return null
    }
    if (n < 2) return 'Add at least two players.'
    if (remote.length && !remoteOk) return 'You and LLM seats can only play a single table, played once.'
    if (format === 'table' && n > 9) return 'A table seats at most 9; use Multi-table for more.'
    if (format === 'table' && duplicate && n !== 2) return 'Duplicate needs exactly two players.'
    if (format === 'mtt' && mode !== 'freezeout') return 'Multi-table is always a freezeout.'
    if (blindKind === 'flat' && !(flat.sb > 0 && flat.sb <= flat.bb)) return 'Small blind must be between 1 and the big blind.'
    if (stack < 1) return 'Starting stack must be positive.'
    if (hands < 1) return 'Hands must be at least 1.'
    return null
  }, [entrants, format, duplicate, mode, blindKind, flat, stack, hands, remote.length, remoteOk, vsExt, g])

  const summary = useMemo(() => {
    const n = entrants.length
    if (vsExt) {
      const who = entrants.find(e => !isExternal(e))
      const watch = pace === 600 ? 'watched live' : pace ? `watched live at a ${PACES.find(p => p.ms === pace)?.title.toLowerCase()} pace`
        : `at full speed, ${g.default_workers} hands at a time`
      return `${who ? label(who) : 'One player'} against ${g.title}: ${fmt(hands)} hands of ${g.starting_stack / g.bb}bb `
        + `at ${fmt(g.sb)}/${fmt(g.bb)}, every hand from ${fmt(g.starting_stack)} chips, ${watch}.`
    }
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
  }, [entrants, format, mode, blindKind, preset, blinds, hands, duplicate, series, runs, pace, stack, tableSize, vsExt, g])

  // Rough wall-clock estimate for paced play (about 7 paced steps per hand).
  const estimate = useMemo(() => {
    const n = entrants.length
    if (vsExt) {
      // paced: ~7 paced steps per hand; full speed: ~1 s per hand per session, several at once
      const minutes = pace ? (hands * 7 * pace) / 60000 : hands / g.default_workers / 60
      return minutes < 2 ? 'about a minute' : minutes < 90 ? `roughly ${Math.round(minutes)} minutes` : `roughly ${(minutes / 60).toFixed(1)} hours`
    }
    if (series || !pace || n < 2) return null
    const legs = (format === 'round_robin' && duplicate) || (format === 'table' && n === 2 && duplicate) ? 2 : 1
    const freeze = (players: number) => Math.min(hands, 60 + 40 * players)
    const perMatch = mode === 'freezeout' ? freeze(format === 'table' ? n : format === 'mtt' ? Math.min(n, tableSize) : 2) : hands
    // Matches play `workers` at a time: a round robin all together, a bracket round by round.
    const w = maxParallel ? Infinity : Math.max(1, workers)
    let waves = legs
    if (format === 'round_robin') waves = Math.ceil((n * (n - 1) / 2) * legs / w)
    if (format === 'bracket') {
      const size = 2 ** Math.ceil(Math.log2(n))
      waves = Math.ceil((n - size / 2) / w)                     // round 1 (the rest have byes)
      for (let m = size / 4; m >= 1; m /= 2) waves += Math.ceil(m / w)
    }
    const totalHands = format === 'mtt' ? freeze(n) : perMatch * waves
    const minutes = (totalHands * 7 * pace) / 60000
    return minutes < 2 ? 'about a minute' : minutes < 90 ? `roughly ${Math.round(minutes)} minutes` : `roughly ${(minutes / 60).toFixed(1)} hours`
  }, [entrants, series, pace, format, duplicate, mode, hands, tableSize, vsExt, g, workers, maxParallel])

  // The most matches that can be in play at once with Max: a round robin's every
  // match, or a bracket's first round.
  const peak = useMemo(() => {
    const n = entrants.length
    const legs = format === 'round_robin' && duplicate ? 2 : 1
    if (format === 'round_robin') return (n * (n - 1) / 2) * legs
    if (format === 'bracket') return Math.max(0, n - 2 ** Math.ceil(Math.log2(Math.max(n, 1))) / 2)
    return 0
  }, [entrants, format, duplicate])

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
      if (vsExt) {
        // GTO Wizard and Slumbot set the game: no mode, stack, blinds or seed to send
        const r = await post('/api/matches', { bots: entrants, hands, pace_ms: pace, docker,
          time_limit_ms: common.time_limit_ms, time_limits: { human: humanSecs * 1000, mcp: llmSecs * 1000 } })
        const me = (r.seats ?? []).find((x: any) => x.kind === 'human')
        if (me) rememberSeat(r.match_key, me.token, me.name)
        location.hash = me ? seatHash(r.match_key, me.token, me.name) : `#/match/${r.match_key}`
      } else if (format === 'table' && !series) {
        const r = await post('/api/matches', { ...common, names: undefined,
          time_limits: { human: humanSecs * 1000, mcp: llmSecs * 1000 } })
        const me = (r.seats ?? []).find((x: any) => x.kind === 'human')
        if (me) rememberSeat(r.match_key, me.token, me.name)
        location.hash = me ? seatHash(r.match_key, me.token, me.name) : `#/match/${r.match_key}`
      } else {
        const r = await post('/api/runs', {
          ...common, format, name: name.trim() || undefined, runs, workers: maxParallel ? 'max' : workers,
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
              {externals.map(x => (
                <div key={x.name} className="bot-row" data-testid={`external-${x.name}`}>
                  <span>{x.title} <span className="muted">({EXTERNAL_BLURB[x.name] ?? 'through its API'}; heads-up)</span></span>
                  <span className="muted mono">api</span><span />
                  <button className="small" disabled={!x.configured || !remoteOk || vsExt}
                          title={!x.configured ? (x.why ?? 'Not set up on this server')
                            : remoteOk ? 'Plays one heads-up match against one other player' : 'Only for a single table, played once'}
                          onClick={() => add(x.name)} data-testid={`add-${x.name}`}>Add</button>
                </div>
              ))}
              {bots.map(b => (
                <div key={b.path} className="bot-row">
                  <span>{b.name}</span>
                  <span className="muted mono clip" title={folderTitle(b.folder)}>{folderLabel(b.folder)}</span>
                  <span className="muted mono" title="code version">{b.hash.slice(0, 8)}</span>
                  <button className="small" onClick={() => add(b.name)} data-testid={`add-${b.name}`}>Add</button>
                </div>
              ))}
            </div>
            <button className="ghost small" style={{ marginTop: 8 }}
                    onClick={() => setEntrants(es => [...es, ...bots.map(b => b.name)])}>Add every bot</button>
          </section>

          {vsExt ? (
          <section className="section" data-testid="external-game">
            <h2>{g.title}'s game</h2>
            <p className="hint" style={{ marginTop: 0 }}>
              Heads-up, {g.starting_stack / g.bb}bb deep: {fmt(g.starting_stack)} chips at {fmt(g.sb)}/{fmt(g.bb)},
              and every hand starts from that stack (cash). {g.title} deals on its own server, so there is no seed
              and no duplicate; judge the result by the luck-adjusted bb/100 ({g.score_kind === 'AIVAT' ? 'AIVAT'
                : "Slumbot's baseline: your result minus what Slumbot won with your cards against itself"}), shown in the standings.
            </p>
            <div className="fields">
              <label>Hands
                <input type="number" min={1} value={hands} onChange={e => setHands(Number(e.target.value))} data-testid="hands" /></label>
            </div>
            <p className="hint">{g.name === 'gto_wizard'
              ? "Every hand counts against the API key's monthly quota (about 100,000 hands)."
              : `Slumbot is a free service run by one person, so at most ${(ext as ExternalBot | null)?.max_workers ?? 3} hands play at once.`}</p>
          </section>
          ) : <>
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
          </>}

          <section className="section">
            <h2>Watching and repeats</h2>
            <div className="fields">
              <label>Play it
                <select value={runs > 1 ? 'series' : 'once'} onChange={e => setRuns(e.target.value === 'series' ? 100 : 1)} data-testid="repeat">
                  <option value="once">Once</option>
                  <option value="series">Many times (a series)</option>
                </select></label>
              {series && <label>Runs<input type="number" min={2} value={runs} onChange={e => setRuns(Math.max(2, Number(e.target.value)))} data-testid="runs" /></label>}
              {(series || (format !== 'table' && format !== 'mtt')) && <label>{series ? 'Runs in parallel' : 'Matches in parallel'}
                {maxParallel ? <input value="all" disabled data-testid="workers" />
                  : <input type="number" min={1} max={32} value={workers}
                           onChange={e => setWorkers(Math.max(1, Number(e.target.value)))} data-testid="workers" />}</label>}
              {(series || (format !== 'table' && format !== 'mtt')) && (
                <label className="check" style={{ alignSelf: 'end' }}>
                  <input type="checkbox" checked={maxParallel} onChange={e => setMaxParallel(e.target.checked)}
                         data-testid="workers-max" />
                  Max {series ? '(one run per CPU)' : '(as many as the tournament allows)'}</label>
              )}
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
              : PACES.find(p => p.ms === pace)?.blurb + '.'}
              {!series && format !== 'table' && format !== 'mtt' && (maxParallel || workers > 1) &&
                ` ${maxParallel ? (format === 'round_robin' ? `All ${peak || ''} matches play at once`
                  : 'Every match in a round plays at once; the next round starts when it ends')
                  : `Up to ${workers} matches play at once`}${pace ? ', each at this pace; follow any of them from Now playing on the run page' : ''}.`}
              {!series && maxParallel && cpus && peak > cpus &&
                ` That is more matches than this machine's ${cpus} CPUs, so slow bots may run into their time limit.`}</p>
          </section>

          <details className="section">
            <summary className="muted" style={{ cursor: 'pointer' }}>Advanced</summary>
            <div className="fields" style={{ marginTop: 10 }}>
              {format !== 'table' && <label>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="optional" /></label>}
              {!vsExt && <label>Seed<input value={seed} onChange={e => setSeed(e.target.value.replace(/\D/g, ''))} placeholder="random" data-testid="seed" /></label>}
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
  if (entrant in EXTERNAL_TITLE) return EXTERNAL_TITLE[entrant]
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

