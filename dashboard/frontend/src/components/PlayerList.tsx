import { Skull, Shield, Heart, Search, Users } from 'lucide-react'
import type { GameData, PlayerInfo, Role } from '../types'
import { ROLE_STYLES } from '../types'

const ROLE_ICON: Record<Role, React.ReactNode> = {
  mafia: <Skull className="w-3.5 h-3.5" />,
  detective: <Search className="w-3.5 h-3.5" />,
  doctor: <Heart className="w-3.5 h-3.5" />,
  villager: <Users className="w-3.5 h-3.5" />,
}

function isAlive(pid: string, data: GameData): boolean {
  return !data.deaths.some(d => d.target === pid)
}

export function PlayerList({ data }: { data: GameData }) {
  const players = Object.entries(data.players) as [string, PlayerInfo][]
  if (players.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2">
      {players.map(([pid, p]) => {
        const alive = isAlive(pid, data)
        const style = ROLE_STYLES[p.role]
        return (
          <div
            key={pid}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border ring-1 ring-inset transition-opacity ${style.badge} ${!alive ? 'opacity-40 line-through' : ''}`}
          >
            {!alive && <Skull className="w-3 h-3 text-red-400 flex-shrink-0" />}
            {alive && <Shield className="w-3 h-3 flex-shrink-0 opacity-50" />}
            <span className="text-sm font-mono">{p.short}</span>
            <span className="flex items-center gap-0.5 text-xs opacity-80">
              {ROLE_ICON[p.role]}
              {p.role}
            </span>
          </div>
        )
      })}
    </div>
  )
}
