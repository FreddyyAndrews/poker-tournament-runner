import type { TableState } from '../replay/state'
import { ord } from './PokerTable'

const fmt = (n: number) => n.toLocaleString('en-US')

export function Standings({ state, colorOf }: { state: TableState; colorOf: (n: string) => string }) {
  const start = state.start?.config.starting_stack ?? 0
  // Cash games reset stacks every hand, so the score is the running net, not the stack.
  const cash = state.start?.config.mode === 'cash'
  const score = (p: (typeof state.players)[string]) => cash ? p.net : p.stack
  // Against an external bot: luck-adjusted bb/100, which says far more than net chips
  const bb = state.blinds?.bb ?? state.start?.config.structure.levels[0]?.bb ?? 100
  const scored = Object.values(state.players).some(p => p.score !== null)
  const kind = state.start?.score_kind ?? 'AIVAT'
  const handsDone = (state.hand ?? -1) + (state.handOver ? 1 : 0)
  const perHundred = (v: number | null) => v === null || !handsDone ? '' :
    ((v / bb) * 100 / handsDone > 0 ? '+' : '') + ((v / bb) * 100 / handsDone).toFixed(1)
  const rows = Object.values(state.players).sort((a, b) =>
    (a.busted ? 1 : 0) - (b.busted ? 1 : 0) || score(b) - score(a) || (a.place ?? 99) - (b.place ?? 99))
  return (
    <table className="standings num" data-testid="standings">
      {cash && <thead><tr><th /><th className="r" title="Chips in front of the player this hand; every hand starts from the starting stack">This hand</th>
        <th className="r" title="Chips won over the match: the score in a cash game">Net</th>
        {scored && <th className="r" title={kind === 'AIVAT' ? "Luck-adjusted bb/100 (GTO Wizard's AIVAT score)"
          : "Luck-adjusted bb/100 (your result minus what Slumbot won with your cards against itself)"}>
          {kind === 'AIVAT' ? 'AIVAT' : 'Adj.'} bb/100</th>}</tr></thead>}
      <tbody>
        {rows.map(p => {
          const delta = cash ? p.net : p.stack - start
          return (
            <tr key={p.name} className={p.busted ? 'out' : ''}>
              <td><span className="who"><span className="swatch" style={{ background: colorOf(p.name) }} />{p.name}</span></td>
              <td className="r">{p.busted && p.place ? `out, ${ord(p.place)}` : fmt(p.stack)}</td>
              <td className="r" style={{ color: 'var(--text-2)' }}>{cash && delta === 0 ? '\u00b10' : (delta > 0 ? '+' : '') + fmt(delta)}</td>
              {scored && <td className="r" data-testid={`score-${p.name}`}>{perHundred(p.score)}</td>}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
