import { useMemo, useRef, useState } from 'react'
import type { PokerEvent } from '../types'

const fmt = (n: number) => n.toLocaleString('en-US')
const short = (n: number) => n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(1)}k` : `${n}`

type Props = {
  events: PokerEvent[]
  cursor: number
  names: string[]                    // series order (= seat order, fixes colours)
  start: number                      // starting stack
  cash?: boolean                     // cash game: plot the running net instead of stacks
  colorOf: (name: string) => string
  onSeekHand: (hand: number) => void
}

const W = 340, H = 170, PAD = { l: 38, r: 64, t: 8, b: 20 }

// Stacks after each hand, up to the cursor (or, in a cash game, where stacks reset
// every hand, each bot's running net). Hover: crosshair + every bot's value.
export function StackChart({ events, cursor, names, start, cash = false, colorOf, onSeekHand }: Props) {
  const [hover, setHover] = useState<number | null>(null)
  const ref = useRef<SVGSVGElement>(null)

  const points = useMemo(() => {
    const pts: { hand: number | null; stacks: Record<string, number> }[] = [
      { hand: null, stacks: Object.fromEntries(names.map(n => [n, cash ? 0 : start])) }]
    let last = pts[0].stacks
    for (let i = 0; i <= cursor && i < events.length; i++) {
      const e = events[i]
      if (e.type === 'hand_end') {
        if (cash) {
          last = { ...last }
          for (const [n, d] of Object.entries(e.deltas)) last[n] = (last[n] ?? 0) + d
        } else {
          last = { ...last, ...e.stacks }
        }
        pts.push({ hand: e.hand, stacks: last })
      }
    }
    return pts
  }, [events, cursor, names, start, cash])

  // Stacks: 0 to at least twice the start. Net: symmetric around 0 so up and down read the same.
  const values = points.flatMap(p => Object.values(p.stacks))
  const extent = Math.max(1, ...values.map(Math.abs))
  const maxY = cash ? extent : Math.max(start * 2, ...values)
  const minY = cash ? -extent : 0
  const xs = (i: number) => PAD.l + (points.length > 1 ? (i / (points.length - 1)) * (W - PAD.l - PAD.r) : 0)
  const ys = (v: number) => PAD.t + (1 - (v - minY) / (maxY - minY)) * (H - PAD.t - PAD.b)
  const ticks = [minY, (minY + maxY) / 2, maxY].map(v => Math.round(v))
  const signed = (n: number) => (cash && n > 0 ? '+' : '') + fmt(n)

  // Direct labels at the line ends (<= 4 series), nudged apart vertically.
  const ends = names.map(n => ({ n, y: ys(points[points.length - 1].stacks[n] ?? 0) }))
    .sort((a, b) => a.y - b.y)
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 12) ends[i].y = ends[i - 1].y + 12

  const onMove = (ev: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    const x = ((ev.clientX - r.left) / r.width) * W
    const i = Math.round(((x - PAD.l) / (W - PAD.l - PAD.r)) * (points.length - 1))
    setHover(Math.max(0, Math.min(points.length - 1, i)))
  }
  const hp = hover !== null ? points[hover] : null

  if (points.length < 2) return <div className="empty">The chart starts after the first hand.</div>
  return (
    <div className="chart" data-testid="stack-chart" data-mode={cash ? 'net' : 'stacks'}>
      {cash && <div className="muted small" style={{ marginBottom: 4 }}>Net chips over the match (stacks reset every hand in a cash game)</div>}
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} onPointerMove={onMove} onPointerLeave={() => setHover(null)}
           onClick={() => hp && hp.hand !== null && onSeekHand(hp.hand)} style={{ cursor: 'pointer' }}
           role="img" aria-label={cash ? 'Net chips by hand' : 'Stack sizes by hand'}>
        <g className="grid axis">
          {ticks.map(t => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={ys(t)} y2={ys(t)} />
              <text x={PAD.l - 6} y={ys(t) + 4} textAnchor="end" className="num">
                {t < 0 ? '-' + short(-t) : (cash && t > 0 ? '+' : '') + short(t)}</text>
            </g>
          ))}
          <text x={PAD.l} y={H - 4} className="num">start</text>
          <text x={W - PAD.r} y={H - 4} textAnchor="end" className="num">
            hand {(points[points.length - 1].hand ?? 0) + 1}
          </text>
        </g>
        {names.map(n => (
          <polyline key={n} className="series" stroke={colorOf(n)}
                    points={points.map((p, i) => `${xs(i)},${ys(p.stacks[n] ?? 0)}`).join(' ')} />
        ))}
        {names.length <= 4 && ends.map(({ n, y }) => (
          <text key={n} x={W - PAD.r + 6} y={y + 4} fill="var(--text-2)" fontSize="11">{n}</text>
        ))}
        {hover !== null && <line className="cross" x1={xs(hover)} x2={xs(hover)} y1={PAD.t} y2={H - PAD.b} />}
      </svg>
      {hp && (
        <div className="tip num" style={{ left: `${Math.min(xs(hover!) / W * 100, 55)}%`, top: 0 }}>
          <div className="muted" style={{ marginBottom: 4 }}>{hp.hand === null ? 'Start' : `After hand ${hp.hand + 1}`}</div>
          {[...names].sort((a, b) => (hp.stacks[b] ?? 0) - (hp.stacks[a] ?? 0)).map(n => (
            <div className="row" key={n}>
              <span className="key" style={{ background: colorOf(n) }} />
              <span className="sub">{n}</span>
              <span className="v">{signed(hp.stacks[n] ?? 0)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="legend">
        {names.map(n => <span key={n}><i style={{ background: colorOf(n) }} />{n}</span>)}
      </div>
    </div>
  )
}
