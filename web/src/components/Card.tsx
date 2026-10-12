// Suits as text glyphs with the text-presentation selector, so no platform
// renders them as emoji.
const SUIT: Record<string, string> = { s: '♠︎', h: '♥︎', d: '♦︎', c: '♣︎' }
const SUIT_NAME: Record<string, string> = { s: 'spades', h: 'hearts', d: 'diamonds', c: 'clubs' }
const RANK_NAME: Record<string, string> = { T: '10', J: 'J', Q: 'Q', K: 'K', A: 'A' }

type Props = { card?: string | null; size?: 'sm' | 'md' | 'lg'; back?: boolean; dim?: boolean }

export function Card({ card, size = 'md', back, dim }: Props) {
  const cls = ['card', size !== 'md' ? size : '', dim ? 'dim' : ''].filter(Boolean).join(' ')
  if (back) return <div className={cls + ' back'} aria-label="hidden card" />
  if (!card) return <div className={cls + ' slot'} aria-hidden />
  const rank = RANK_NAME[card[0]] ?? card[0]
  const suit = card[1]
  const red = suit === 'h' || suit === 'd'
  return (
    <div className={cls + (red ? ' red' : '')} aria-label={`${rank} of ${SUIT_NAME[suit]}`}
         data-testid="card" data-card={card}>
      <span className="rank">{rank}</span>
      <span className="suit">{SUIT[suit]}</span>
    </div>
  )
}

export function Cards({ cards, size = 'sm' }: { cards: string[]; size?: 'sm' | 'md' | 'lg' }) {
  return <span style={{ display: 'inline-flex', gap: 3 }}>{cards.map(c => <Card key={c} card={c} size={size} />)}</span>
}
