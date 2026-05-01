import { RefreshCw, Wifi, WifiOff, Trophy } from 'lucide-react'
import type { GameData } from '../types'
import { PlayerList } from './PlayerList'

interface Props {
  data: GameData
  autoRefresh: boolean
  onToggleRefresh: () => void
  onRefresh: () => void
  loading: boolean
}

export function GameHeader({ data, autoRefresh, onToggleRefresh, onRefresh, loading }: Props) {
  const playerCount = Object.keys(data.players).length
  const outcome = data.outcome
  const phaseCount = data.timeline.length

  return (
    <header className="border-b border-white/5 bg-[#0d1220]/80 backdrop-blur-sm">
      <div className="flex items-center gap-4 px-5 py-3">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-red-500 to-purple-600 flex items-center justify-center text-white text-sm font-bold">
            M
          </div>
          <div>
            <h1 className="text-sm font-bold text-white tracking-wide">Mafia on Intent Space</h1>
            <p className="text-[11px] text-slate-500">
              {playerCount > 0 ? `${playerCount} players` : 'No game loaded'}
              {phaseCount > 0 && ` \u00b7 ${phaseCount} phases`}
            </p>
          </div>
        </div>

        <div className="flex-1" />

        {outcome ? (
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-semibold ${
            outcome.winner === 'mafia'
              ? 'bg-red-500/15 text-red-400'
              : outcome.winner === 'villagers'
                ? 'bg-green-500/15 text-green-400'
                : 'bg-amber-500/15 text-amber-400'
          }`}>
            <Trophy className="w-4 h-4" />
            {outcome.winner === 'mafia' ? 'Mafia Won' : outcome.winner === 'villagers' ? 'Villagers Won' : 'Draw'}
          </div>
        ) : playerCount > 0 ? (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/15 text-emerald-400 text-sm font-medium">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            In Progress
          </div>
        ) : null}

        <div className="flex items-center gap-1">
          <button
            onClick={onToggleRefresh}
            className={`p-1.5 rounded-lg transition-colors ${
              autoRefresh ? 'text-green-400 hover:bg-green-500/10' : 'text-slate-600 hover:bg-white/5'
            }`}
            title={autoRefresh ? 'Auto-refresh ON' : 'Auto-refresh OFF'}
          >
            {autoRefresh ? <Wifi className="w-4 h-4" /> : <WifiOff className="w-4 h-4" />}
          </button>
          <button
            onClick={onRefresh}
            disabled={loading}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition-colors disabled:opacity-30"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {playerCount > 0 && (
        <div className="px-5 pb-3">
          <PlayerList data={data} />
        </div>
      )}
    </header>
  )
}
