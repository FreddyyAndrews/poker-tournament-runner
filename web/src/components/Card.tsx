// Suits as text glyphs with the text-presentation selector, so no platform
// renders them as emoji.
const SUIT: Record<string, string> = { s: '♠︎', h: '♥︎', d: '♦︎', c: '♣︎' }
const SUIT_NAME: Record<string, string> = { s: 'spades', h: 'hearts', d: 'diamonds', c: 'clubs' }
const RANK_NAME: Record<string, string> = { T: '10', J: 'J', Q: 'Q', K: 'K', A: 'A' }

type Props = { card?: string | null; size?: 'sm' | 'md' | 'lg'; back?: boolean; dim?: boolean }

export function Card({ card, size = 'md', back, dim }: Props) {
  const cls = ['card', size !== 'md' ? size : '', dim ? 'dim' : ''].filter(Boolean).join(' ')
  if (back) return <div className={cls + ' back'} aria-label="face-down card" data-testid="card-back"><CardBack /></div>
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

// The back of a card: a flat diamond lattice inside a white border (no gradients).
function CardBack() {
  return (
    <svg viewBox="0 0 40 56" preserveAspectRatio="none" width="100%" height="100%" aria-hidden>
      <defs>
        <pattern id="card-lattice" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <path d="M0 0H5M0 0V5" stroke="rgba(255,255,255,0.38)" strokeWidth="0.9" />
        </pattern>
      </defs>
      <rect x="0" y="0" width="40" height="56" rx="4" fill="var(--card-back)" />
      <rect x="3" y="3" width="34" height="50" rx="2.5" fill="url(#card-lattice)"
            stroke="rgba(255,255,255,0.85)" strokeWidth="1.2" />
      <rect x="14" y="22" width="12" height="12" transform="rotate(45 20 28)" fill="var(--card-back)"
            stroke="rgba(255,255,255,0.85)" strokeWidth="1" />
    </svg>
  )
}
