// Poker chips: an amount drawn as columns of coloured chips, one column per
// denomination, largest first. Big stacks use big denominations, so even very
// large amounts stay a handful of short columns. Flat SVG, no gradients. The
// exact amount is always shown as text next to the chips by the caller.

export const DENOMS: { value: number; top: string; side: string; label: string }[] = [
  { value: 1_000_000, top: '#e8e6e1', side: '#b9b6ae', label: '1M' },
  { value: 100_000, top: '#d55181', side: '#a83a63', label: '100k' },
  { value: 25_000, top: '#3987e5', side: '#2763ae', label: '25k' },
  { value: 5_000, top: '#e2701f', side: '#ad5313', label: '5k' },
  { value: 1_000, top: '#e0b13a', side: '#ac8424', label: '1k' },
  { value: 500, top: '#8a63d6', side: '#6646a6', label: '500' },
  { value: 100, top: '#2a2c31', side: '#141518', label: '100' },
  { value: 25, top: '#1f8a4c', side: '#146536', label: '25' },
  { value: 5, top: '#c8312f', side: '#951f1e', label: '5' },
  { value: 1, top: '#f4f3ef', side: '#c5c3bc', label: '1' },
]

const RX = 9          // chip radius (horizontal)
const RY = 4          // perspective squash
const T = 3           // chip thickness
const COL_GAP = 3

/** Chips per denomination for an amount: greedy, biggest first (starting at a
 * chip that makes at least 3), capped so a pile never gets silly; the smallest
 * leftovers are dropped from the drawing. */
export function breakdown(amount: number, maxPerColumn = 10, maxColumns = 4) {
  const cols: { d: (typeof DENOMS)[number]; n: number }[] = []
  let left = Math.max(0, Math.floor(amount))
  for (const [i, d] of DENOMS.entries()) {
    if (left < d.value) continue
    const n = Math.floor(left / d.value)
    // a big top chip (1k+) must make a real column (3+), or a large stack looks
    // like two lonely chips; small amounts keep their natural chips
    if (!cols.length && n < 3 && d.value >= 1000 && i < DENOMS.length - 1) continue
    left -= n * d.value
    cols.push({ d, n: Math.min(n, maxPerColumn) })
    if (cols.length === maxColumns) break
  }
  return cols
}

function Chip({ cx, y, d }: { cx: number; y: number; d: (typeof DENOMS)[number] }) {
  // side band, then the top face with edge spots
  return (
    <g>
      <ellipse cx={cx} cy={y + T} rx={RX} ry={RY} fill={d.side} />
      <rect x={cx - RX} y={y} width={RX * 2} height={T} fill={d.side} />
      {[-0.66, 0, 0.66].map(f => (
        <rect key={f} x={cx + f * RX - 1} y={y + 0.5} width={2} height={T - 0.5} fill="rgba(255,255,255,0.55)" />
      ))}
      <ellipse cx={cx} cy={y} rx={RX} ry={RY} fill={d.top} stroke="rgba(0,0,0,0.25)" strokeWidth={0.5} />
      <ellipse cx={cx} cy={y} rx={RX - 2.2} ry={RY - 1.1} fill="none" stroke="rgba(255,255,255,0.75)"
               strokeWidth={1.1} strokeDasharray="2.2 2.2" />
    </g>
  )
}

type Props = {
  amount: number
  maxPerColumn?: number
  maxColumns?: number
  title?: string
}

export function ChipStack({ amount, maxPerColumn = 10, maxColumns = 4, title }: Props) {
  const cols = breakdown(amount, maxPerColumn, maxColumns)
  if (!cols.length) return null
  const tallest = Math.max(...cols.map(c => c.n))
  const w = cols.length * (RX * 2) + (cols.length - 1) * COL_GAP + 2
  const h = tallest * T + RY * 2 + T + 2
  const base = h - RY - T - 1          // y of the bottom chip's top face
  return (
    <svg className="chips" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img"
         aria-label={title ?? `${amount.toLocaleString('en-US')} in chips`}>
      <title>{`${amount.toLocaleString('en-US')} (${cols.map(c => `${c.n} \u00d7 ${c.d.label}`).join(', ')})`}</title>
      {cols.map((c, ci) => {
        const cx = 1 + RX + ci * (RX * 2 + COL_GAP)
        return (
          <g key={c.d.value}>
            {Array.from({ length: c.n }, (_, i) => <Chip key={i} cx={cx} y={base - i * T} d={c.d} />)}
          </g>
        )
      })}
    </svg>
  )
}
