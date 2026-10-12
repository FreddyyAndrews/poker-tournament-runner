import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { Controls } from '../components/Controls'
import { HandLog } from '../components/HandLog'
import { Inspector } from '../components/Inspector'
import { PokerTable, playerColor } from '../components/PokerTable'
import { StackChart } from '../components/StackChart'
import { Standings } from '../components/Standings'
import { useMatch } from '../replay/useMatch'
import { usePlayback } from '../replay/usePlayback'

type Tab = 'hand' | 'decision' | 'chart'

export function MatchView({ matchKey }: { matchKey: string }) {
  const data = useMatch(matchKey)
  const { timeline, length, status } = data
  const running = status === 'running'
  const events = useCallback(() => timeline.list, [timeline])
  const pb = usePlayback(events, length, running, running)
  const [tab, setTab] = useState<Tab>('hand')
  const [showAll, setShowAll] = useState(true)

  const state = useMemo(() => timeline.stateAt(pb.cursor), [timeline, pb.cursor, length])
  const handStarts = useMemo(() => {
    const out: number[] = []
    timeline.list.forEach((e, i) => { if (e.type === 'hand_start') out.push(i) })
    return out
  }, [timeline, length])
  const handFrom = useMemo(() => {
    let from = 0
    for (const i of handStarts) { if (i <= pb.cursor) from = i; else break }
    return from
  }, [handStarts, pb.cursor])

  const start = timeline.list[0]?.type === 'match_start' ? timeline.list[0] : null
  const names = useMemo(() => start ? start.seats.map(s => s.name) : [], [start])
  const colorOf = useCallback((n: string) => playerColor(Math.max(0, names.indexOf(n))), [names])
  const totalHands = data.meta?.summary?.hands ?? handStarts.length

  // Keyboard: space play/pause, arrows step, shift+arrows hands, L live.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT') return
      if (e.key === ' ') { e.preventDefault(); pb.toggle() }
      else if (e.key === 'ArrowRight' && !e.shiftKey) pb.step(1)
      else if (e.key === 'ArrowLeft' && !e.shiftKey) pb.step(-1)
      else if (e.key === 'ArrowRight' && e.shiftKey) pb.seek(handStarts.find(i => i > pb.cursor) ?? length - 1)
      else if (e.key === 'ArrowLeft' && e.shiftKey) pb.seek([...handStarts].reverse().find(i => i < pb.cursor) ?? 0)
      else if (e.key.toLowerCase() === 'l' && running) pb.goLive()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pb, handStarts, length, running])

  if (data.error) return <div className="container"><h1>Match not found</h1><p className="muted">{data.error}</p></div>

  const cfg = start?.config
  const jobId = matchKey.split('/')[0]
  return (
    <div className="match">
      <div className="stage">
        <div className="stage-head">
          <span className="title" data-testid="match-title">{matchKey.split('/').pop()}</span>
          {running ? <span className="pill live" data-testid="status"><span className="dot" />Live</span>
            : status === 'complete' ? <span className="pill" data-testid="status">Complete</span>
            : status === 'incomplete' ? <span className="pill" data-testid="status">Incomplete</span> : null}
          {cfg && (
            <span className="facts num">
              <span>{cfg.mode === 'freezeout' ? 'Freezeout' : cfg.mode === 'cash' ? `Cash, ${cfg.hands} hands` : `${cfg.hands} hands`}</span>
              <span>Stack {cfg.starting_stack.toLocaleString('en-US')}</span>
              <span>{cfg.structure.name}</span>
              <span className="muted">seed {cfg.seed}</span>
            </span>
          )}
          <span className="spacer" />
          <button className="ghost" onClick={() => setShowAll(v => !v)} data-testid="toggle-cards"
                  title="Spectator view shows every hole card; fair view hides them until showdown">
            {showAll ? 'Hide hole cards' : 'Show hole cards'}
          </button>
          {running && <button className="ghost" onClick={() => api.cancel(jobId)}>Stop</button>}
          {status === 'complete' && <a href={api.exportUrl(matchKey)}><button className="ghost">Download</button></a>}
        </div>

        <div className="table-wrap">
          <PokerTable state={state} showAll={showAll} colorOf={colorOf} />
        </div>

        <Controls pb={pb} length={length} running={running} handStarts={handStarts}
                  currentHand={state.hand} totalHands={totalHands} />
      </div>

      <aside className="side">
        <div className="panel" style={{ flex: 'none', paddingBottom: 6 }}>
          <h2>Standings</h2>
          <Standings state={state} colorOf={colorOf} />
        </div>
        <div className="tabs" role="tablist">
          {(['hand', 'decision', 'chart'] as Tab[]).map(t => (
            <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)} role="tab"
                    data-testid={`tab-${t}`}>
              {t === 'hand' ? 'Hand log' : t === 'decision' ? 'Decision' : 'Stacks'}
            </button>
          ))}
        </div>
        <div className="panel">
          {tab === 'hand' && <HandLog events={timeline.list} from={handFrom} cursor={pb.cursor}
                                      onPick={i => { pb.seek(i); if (timeline.list[i].type === 'decision') setTab('decision') }} />}
          {tab === 'decision' && <Inspector d={state.lastDecision} />}
          {tab === 'chart' && <StackChart events={timeline.list} cursor={pb.cursor} names={names}
                                          start={cfg?.starting_stack ?? 0} colorOf={colorOf}
                                          onSeekHand={h => pb.seek(handStarts[h] ?? 0)} />}
        </div>
      </aside>
    </div>
  )
}
