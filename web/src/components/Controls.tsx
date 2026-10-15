import { SPEEDS, type Playback } from '../replay/usePlayback'

const Icon = {
  play: <svg viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z" /></svg>,
  pause: <svg viewBox="0 0 16 16"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" /></svg>,
  stepBack: <svg viewBox="0 0 16 16"><path d="M3 3h2v10H3zM13 3v10L6 8z" /></svg>,
  stepFwd: <svg viewBox="0 0 16 16"><path d="M11 3h2v10h-2zM3 3v10l7-5z" /></svg>,
  prevHand: <svg viewBox="0 0 16 16"><path d="M8 3v10L1 8zM15 3v10L8 8z" /></svg>,
  nextHand: <svg viewBox="0 0 16 16"><path d="M8 3v10l7-5zM1 3v10l7-5z" /></svg>,
}

// A notable moment on the timeline, e.g. the start of a hand that went to showdown.
export type Highlight = { index: number; kind: 'showdown'; label: string }

type Props = {
  pb: Playback
  length: number
  running: boolean
  handStarts: number[]       // event indices of hand_start
  currentHand: number | null
  totalHands: number
  highlights?: Highlight[]
}

export function Controls({ pb, length, running, handStarts, currentHand, totalHands, highlights = [] }: Props) {
  const prevHand = () => {
    const before = handStarts.filter(i => i < pb.cursor)
    // within the first few events of a hand, go to the previous hand
    const target = before.length >= 2 && pb.cursor - before[before.length - 1] < 3
      ? before[before.length - 2] : before[before.length - 1]
    pb.seek(target ?? 0)
  }
  const nextHand = () => {
    const after = handStarts.find(i => i > pb.cursor)
    pb.seek(after ?? length - 1)
  }
  const max = Math.max(length - 1, 0)

  return (
    <div className="controls" data-testid="controls">
      {length > 1 && highlights.length > 0 && (
        <div className="marks" data-testid="highlights">
          {highlights.map(h => (
            <button key={h.index} className={`mark ${h.kind}`} title={h.label} aria-label={h.label}
                    style={{ left: `calc(7px + (100% - 14px) * ${h.index / max})` }}
                    onClick={() => pb.seek(h.index)} />
          ))}
        </div>
      )}
      <div className="scrub">
        <div className="ticks">
          {length > 1 && handStarts.length < 400 && handStarts.map(i =>
            <span key={i} style={{ left: `calc(7px + (100% - 14px) * ${i / max})` }} />)}
        </div>
        <input type="range" min={-1} max={max} value={pb.cursor} aria-label="Timeline"
               data-testid="scrubber" onChange={e => pb.seek(Number(e.target.value))} />
      </div>
      <div className="transport">
        <button className="icon-btn" onClick={prevHand} title="Previous hand (Shift+Left)" aria-label="Previous hand">{Icon.prevHand}</button>
        <button className="icon-btn" onClick={() => pb.step(-1)} title="Step back (Left)" aria-label="Step back" data-testid="step-back">{Icon.stepBack}</button>
        <button className="icon-btn" onClick={pb.toggle} title="Play / pause (Space)" aria-label={pb.playing ? 'Pause' : 'Play'} data-testid="play">
          {pb.playing ? Icon.pause : Icon.play}
        </button>
        <button className="icon-btn" onClick={() => pb.step(1)} title="Step (Right)" aria-label="Step" data-testid="step">{Icon.stepFwd}</button>
        <button className="icon-btn" onClick={nextHand} title="Next hand (Shift+Right)" aria-label="Next hand">{Icon.nextHand}</button>
        <div className="speeds" role="group" aria-label="Speed">
          {SPEEDS.map(s => (
            <button key={s} className={pb.speed === s ? 'active' : ''} onClick={() => pb.setSpeed(s)}
                    data-testid={`speed-${s}`}>{s}x</button>
          ))}
        </div>
        <span className="grow" />
        <span className="position num" data-testid="position">
          {currentHand !== null ? `Hand ${currentHand + 1} of ${totalHands}${running ? '+' : ''}` : 'Not started'}
          {' · '}event {pb.cursor + 1} / {length}
        </span>
        {running && (
          <button className={'live-btn' + (pb.live ? ' on' : '')} onClick={pb.goLive} data-testid="live"
                  title="Jump to now (L)">
            <span className="dot" />{pb.live ? 'LIVE' : 'GO LIVE'}
          </button>
        )}
      </div>
    </div>
  )
}
