import type { TableState } from '../replay/state'
import { ord } from './PokerTable'

const fmt = (n: number) => n.toLocaleString('en-US')

export function Standings({ state, colorOf }: { state: TableState; colorOf: (n: string) => string }) {
  const start = state.start?.config.starting_stack ?? 0
  const rows = Object.values(state.players).sort((a, b) =>
    (a.busted ? 1 : 0) - (b.busted ? 1 : 0) || b.stack - a.stack || (a.place ?? 99) - (b.place ?? 99))
  return (
    <table className="standings num" data-testid="standings">
      <tbody>
        {rows.map(p => {
          const delta = p.stack - start
          return (
            <tr key={p.name} className={p.busted ? 'out' : ''}>
              <td><span className="who"><span className="swatch" style={{ background: colorOf(p.name) }} />{p.name}</span></td>
              <td className="r">{p.busted && p.place ? `out, ${ord(p.place)}` : fmt(p.stack)}</td>
              <td className="r" style={{ color: 'var(--text-2)' }}>{delta > 0 ? '+' : ''}{fmt(delta)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
