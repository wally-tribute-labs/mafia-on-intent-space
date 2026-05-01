import { Sun, Moon, Skull, MessageCircle, Vote, Target, Search, Shield } from 'lucide-react'
import type { GameData } from '../types'

function formatPhase(name: string): string {
  return name.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function phaseIcon(phase: string) {
  if (phase.startsWith('night')) return <Moon className="w-3.5 h-3.5 text-indigo-400" />
  return <Sun className="w-3.5 h-3.5 text-sky-400" />
}

function actionIcon(action: string) {
  switch (action) {
    case 'discussion-utterance': return <MessageCircle className="w-3 h-3" />
    case 'vote': return <Vote className="w-3 h-3" />
    case 'coven-target': return <Target className="w-3 h-3 text-red-400" />
    case 'investigate': return <Search className="w-3 h-3 text-blue-400" />
    case 'protect': return <Shield className="w-3 h-3 text-purple-400" />
    default: return null
  }
}

interface Props {
  data: GameData
  activePhase: string | null
  onSelect: (phase: string) => void
}

export function PhaseSidebar({ data, activePhase, onSelect }: Props) {
  const groups: Array<{ dayNum: number; phases: typeof data.timeline }> = []
  for (const entry of data.timeline) {
    const match = entry.phase.match(/^(?:day|night)-(\d+)/)
    const dayNum = match ? parseInt(match[1]) : 0
    let group = groups.find(g => g.dayNum === dayNum)
    if (!group) {
      group = { dayNum, phases: [] }
      groups.push(group)
    }
    group.phases.push(entry)
  }

  return (
    <nav className="flex flex-col gap-2 py-3">
      <button
        onClick={() => onSelect('')}
        className={`mx-2 px-3 py-2 rounded-lg text-left text-sm transition-colors ${
          !activePhase ? 'bg-white/10 text-white' : 'text-slate-400 hover:text-white hover:bg-white/5'
        }`}
      >
        All Phases
      </button>

      {groups.map(group => (
        <div key={group.dayNum}>
          <h3 className="px-3 mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Round {group.dayNum}
          </h3>
          <div className="flex flex-col gap-0.5">
            {group.phases.map(entry => {
              const discussions = entry.messages.filter(m => m.action === 'discussion-utterance').length
              const votes = entry.messages.filter(m => m.action === 'vote').length
              const nightActions = entry.messages.filter(m =>
                ['coven-target', 'investigate', 'protect'].includes(m.action)
              ).length
              const hasDeath = entry.deaths.length > 0

              return (
                <button
                  key={entry.phase}
                  onClick={() => onSelect(entry.phase)}
                  className={`mx-2 px-3 py-2 rounded-lg text-left transition-colors ${
                    activePhase === entry.phase
                      ? 'bg-white/10 text-white'
                      : 'text-slate-400 hover:text-white hover:bg-white/5'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    {phaseIcon(entry.phase)}
                    <span className="text-sm font-medium flex-1 truncate">
                      {formatPhase(entry.phase)}
                    </span>
                    {hasDeath && <Skull className="w-3 h-3 text-red-400 flex-shrink-0" />}
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-[10px] text-slate-600">
                    {discussions > 0 && (
                      <span className="flex items-center gap-0.5">
                        {actionIcon('discussion-utterance')} {discussions}
                      </span>
                    )}
                    {votes > 0 && (
                      <span className="flex items-center gap-0.5">
                        {actionIcon('vote')} {votes}
                      </span>
                    )}
                    {nightActions > 0 && (
                      <span className="flex items-center gap-0.5">
                        {actionIcon('coven-target')} {nightActions}
                      </span>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </nav>
  )
}
