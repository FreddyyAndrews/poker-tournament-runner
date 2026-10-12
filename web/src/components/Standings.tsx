import type { TableState } from '../replay/state'
import { ord } from './PokerTable'

const fmt = (n: number) => n.toLocaleString('en-US')

export function Standings({ state, colorOf }: { state: TableState; colorOf: (n: string) => string }) {
  const start = state.start?.config.starting_stack ?? 0
  // Cash games reset stacks every hand, so the score is the running net, not the stack.
  const cash = state.start?.config.mode === 'cash'
  const score = (p: (typeof state.players)[string]) => cash ? p.net : p.stack
  const rows = Object.values(state.players).sort((a, b) =>
    (a.busted ? 1 : 0) - (b.busted ? 1 : 0) || score(b) - score(a) || (a.place ?? 99) - (b.place ?? 99))
  return (
    <table className="standings num" data-testid="standings">
      {cash && <thead><tr><th /><th className="r">Stack</th><th className="r">Net</th></tr></thead>}
      <tbody>
        {rows.map(p => {
          const delta = cash ? p.net : p.stack - start
          return (
            <tr key={p.name} className={p.busted ? 'out' : ''}>
              <td><span className="who"><span className="swatch" style={{ background: colorOf(p.name) }} />{p.name}</span></td>
              <td className="r">{p.busted && p.place ? `out, ${ord(p.place)}` : fmt(p.stack)}</td>
              <td className="r" style={{ color: 'var(--text-2)' }}>{cash && delta === 0 ? '\u00b10' : (delta > 0 ? '+' : '') + fmt(delta)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
