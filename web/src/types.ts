// Event log schema (mirrors arena/events.py). Per-player data is keyed by bot name.

export type Blinds = { sb: number; bb: number; ante: number }

export type Level = { sb: number; bb: number; ante: number; hands: number }

export type MatchConfig = {
  mode: 'fixed' | 'cash' | 'freezeout'
  hands: number
  starting_stack: number
  structure: { name: string; levels: Level[] }
  seed: number
  time_limit_ms: number | null
}

export type SeatInfo = {
  name: string
  kind: string
  seat?: number
  time_limit_ms: number
  hash?: string
  path?: string
}

export type Result = {
  bot: string
  place: number | null
  final_stack: number
  delta: number
  hands_played: number
  bb_per_100: number
  busted_on_hand: number | null
  decisions?: number
  avg_think_ms?: number
  max_think_ms?: number
  timeouts?: number
  errors?: number
}

export type PlayerView = {
  seat: number
  bot_id: string
  stack: number
  state: string
  is_folded: boolean
  is_all_in: boolean
  bet_this_street: number
}

// What a bot was shown when asked to act.
export type BotState = {
  hand_id: string
  hand_num: number
  street: string
  seat_to_act: number
  your_bot_id: string
  pot: number
  community_cards: string[]
  current_bet: number
  min_raise_to: number
  amount_owed: number
  can_check: boolean
  your_cards: string[]
  your_stack: number
  your_bet_this_street: number
  players: PlayerView[]
  action_log: { seat: number; bot_id: string; action: string; amount: number }[]
  blinds: Blinds
  button_seat: number
  sb_seat: number
  bb_seat: number
  time_limit_ms?: number
  tournament?: Record<string, unknown>
}

type Base = { table?: number }

export type MatchStart = Base & {
  type: 'match_start'
  match_id: string
  seed: number
  config: MatchConfig
  seats: SeatInfo[]
  format?: string
  table_size?: number
  table?: number
}
export type LevelEvent = Base & { type: 'level'; hand: number; level: number; sb: number; bb: number; ante: number }
export type HandStart = Base & {
  type: 'hand_start'
  hand: number
  hand_seed: number
  level: number
  blinds: Blinds
  button: number
  sb: number
  bb: number
  order: string[]
  seats: Record<string, number>
  stacks: Record<string, number>
  hole: Record<string, string[]>
}
export type Post = Base & { type: 'post'; hand: number; bot: string; seat: number; kind: 'ante' | 'sb' | 'bb'; amount: number }
export type Decision = Base & {
  type: 'decision'
  hand: number
  bot: string
  seat: number
  street: string
  state: BotState
  reply: { action: string; amount?: number }
  applied: { action: string; amount: number; pot_after: number; stack_after: number }
  think_ms: number | null
  logs: string
  logs_truncated: boolean
  debug: unknown
  debug_error: string | null
  error: string | null
  error_detail: string | null
}
export type Deal = Base & { type: 'deal'; hand: number; street: string; board: string[] }
export type Showdown = Base & { type: 'showdown'; hand: number; board: string[]; reveal: Record<string, string[]>; ranks: Record<string, string> }
export type Award = Base & { type: 'award'; hand: number; bot: string; amount: number; pot: string }
export type HandEnd = Base & { type: 'hand_end'; hand: number; board: string[]; pot: number; stacks: Record<string, number>; deltas: Record<string, number> }
export type Elimination = Base & { type: 'elimination'; hand: number; bot: string; place: number }
export type SeatChange = Base & { type: 'seat_change'; hand: number | null; bot: string; seat: number; stack: number; reason: string }
export type TableClosed = Base & { type: 'table_closed'; hand: number }
export type MatchEnd = Base & { type: 'match_end'; hands: number; duration_s: number; results: Result[]; stopped: string }

export type PokerEvent =
  | MatchStart | LevelEvent | HandStart | Post | Decision | Deal | Showdown | Award
  | HandEnd | Elimination | SeatChange | TableClosed | MatchEnd

export type MatchStatus = 'running' | 'complete' | 'incomplete'

export type MatchMeta = {
  key: string
  status: MatchStatus
  summary: { results: Result[]; stopped: string; hands: number; duration_s: number } | null
  match_start: MatchStart | null
  events: number
}

export type RunRow = {
  id: string
  kind: 'match' | 'run' | 'series'
  status: string
  format: string | null
  name: string | null
  bots: string[]
  runs: number | null
  modified: number
  winner: string | null
}
