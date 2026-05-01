export type Role = 'mafia' | 'detective' | 'doctor' | 'villager'
export type Winner = 'mafia' | 'villagers' | 'draw'

export interface PlayerInfo {
  principal_id: string
  short: string
  role: Role
  bot_slot?: string
}

export interface BotAction {
  ts: number
  action: string
  phase: string
  sender: string
  sender_short: string
  target: string
  target_short: string
  content: string
  reasoning: string
  post_id: string
  bot_slot: string
}

export interface DeathEvent {
  target: string
  target_short: string
  cause: string
  ts: number
}

export interface VoteResult {
  phase: string
  ts: number
  tally?: Record<string, number>
  lynched?: string
}

export interface PhaseEntry {
  phase: string
  ts: number
  living?: string[]
  messages: BotAction[]
  deaths: DeathEvent[]
  vote_result?: VoteResult | null
}

export interface SpaceMeta {
  space_id: string
  label: string
  observatory_url?: string
}

export interface GameData {
  status: 'ok' | 'waiting'
  message?: string
  fetched_at?: number
  game_space_id?: string
  coven_id?: string
  detective_id?: string
  doctor_id?: string
  players: Record<string, PlayerInfo>
  timeline: PhaseEntry[]
  deaths: DeathEvent[]
  outcome?: { winner: Winner; ts: number } | null
  spaces_meta: SpaceMeta[]
}

export const ROLE_STYLES: Record<Role, { dot: string; text: string; badge: string; bg: string; border: string }> = {
  mafia:     { dot: 'bg-mafia-500',     text: 'text-mafia-400',     badge: 'bg-mafia-500/20 text-mafia-400 ring-mafia-500/30',         bg: 'bg-mafia-900/30',     border: 'border-mafia-500/20' },
  detective: { dot: 'bg-detective-500',  text: 'text-detective-400', badge: 'bg-detective-500/20 text-detective-400 ring-detective-500/30', bg: 'bg-detective-900/30', border: 'border-detective-500/20' },
  doctor:    { dot: 'bg-doctor-500',     text: 'text-doctor-400',    badge: 'bg-doctor-500/20 text-doctor-400 ring-doctor-500/30',       bg: 'bg-doctor-900/30',    border: 'border-doctor-500/20' },
  villager:  { dot: 'bg-village-500',    text: 'text-village-400',   badge: 'bg-village-500/20 text-village-400 ring-village-500/30',     bg: 'bg-village-900/30',   border: 'border-village-500/20' },
}
