import { Sun, Moon, Skull, Trophy } from 'lucide-react'
import type { GameData } from '../types'

function formatPhase(name: string): string {
  return name
    .replace(/^(day|night)-(\d+)-(discussion|vote|mafia|detective|doctor)$/i, (_m, period, num) =>
      `${period === 'day' ? 'Day' : 'Night'} ${num}`
    )
    .replace(/-/g, ' ')
}

interface Props {
  data: GameData
  onPhaseClick: (phase: string) => void
  activePhase: string | null
}

export function PhaseTimeline({ data, onPhaseClick, activePhase }: Props) {
  const seenRoots = new Set<string>()
  const events: Array<{ key: string; label: string; phase: string; isNight: boolean; hasDeath: boolean; isEnd: boolean }> = []

  for (const entry of data.timeline) {
    const root = entry.phase.replace(/-(discussion|vote|mafia|detective|doctor)$/, '')
    if (seenRoots.has(root)) continue
    seenRoots.add(root)

    const isNight = entry.phase.startsWith('night')
    const roundEntries = data.timeline.filter(e => e.phase.startsWith(root))
    const hasDeath = roundEntries.some(e => e.deaths.length > 0)

    events.push({
      key: root,
      label: formatPhase(root),
      phase: root,
      isNight,
      hasDeath,
      isEnd: false,
    })
  }

  if (data.outcome) {
    events.push({
      key: 'game-end',
      label: `${data.outcome.winner === 'mafia' ? 'Mafia' : 'Village'} Win`,
      phase: 'game-end',
      isNight: false,
      hasDeath: false,
      isEnd: true,
    })
  }

  return (
    <div className="flex items-center gap-1 overflow-x-auto scrollbar-thin px-1 py-2">
      {events.map((ev, i) => (
        <button
          key={ev.key}
          onClick={() => onPhaseClick(ev.phase)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-all ${
            activePhase === ev.phase
              ? ev.isEnd
                ? 'bg-amber-500/30 text-amber-200 ring-1 ring-amber-500/50'
                : ev.isNight
                  ? 'bg-indigo-500/30 text-indigo-200 ring-1 ring-indigo-500/50'
                  : 'bg-sky-500/30 text-sky-200 ring-1 ring-sky-500/50'
              : ev.isEnd
                ? 'bg-amber-500/10 text-amber-300/60 hover:text-amber-200 hover:bg-amber-500/20'
                : ev.isNight
                  ? 'bg-indigo-500/10 text-indigo-300/60 hover:text-indigo-200 hover:bg-indigo-500/20'
                  : 'bg-sky-500/10 text-sky-300/60 hover:text-sky-200 hover:bg-sky-500/20'
          }`}
        >
          {ev.isEnd ? (
            <Trophy className="w-3 h-3" />
          ) : ev.isNight ? (
            <Moon className="w-3 h-3" />
          ) : (
            <Sun className="w-3 h-3" />
          )}
          {ev.label}
          {ev.hasDeath && <Skull className="w-3 h-3 text-red-400" />}
          {i < events.length - 1 && <span className="ml-1 text-slate-600">{'>'}</span>}
        </button>
      ))}
    </div>
  )
}
