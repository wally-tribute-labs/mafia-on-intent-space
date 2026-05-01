import { useMemo, useRef, useEffect } from 'react'
import {
  MessageCircle, Skull, Vote, Moon, Sun, Target,
  Search, Shield, Trophy,
} from 'lucide-react'
import type { GameData, BotAction, DeathEvent, VoteResult, PhaseEntry, Role } from '../types'
import { ROLE_STYLES } from '../types'

type Filter = 'all' | 'discussion' | 'votes' | 'night' | 'system'

function actionMatchesFilter(action: string, f: Filter): boolean {
  switch (f) {
    case 'all': return true
    case 'discussion': return action === 'discussion-utterance'
    case 'votes': return action === 'vote'
    case 'night': return ['coven-target', 'investigate', 'protect'].includes(action)
    case 'system': return action === 'game-end'
  }
}

interface Props {
  data: GameData
  filter: Filter
  onFilterChange: (f: Filter) => void
  activePhase: string | null
}

export function ChatView({ data, filter, onFilterChange, activePhase }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  const visiblePhases = useMemo(() => {
    if (!activePhase) return data.timeline
    return data.timeline.filter(e => e.phase.startsWith(activePhase))
  }, [data.timeline, activePhase])

  const totalMessages = useMemo(() => {
    let count = 0
    for (const phase of visiblePhases) {
      count += phase.messages.filter(m => actionMatchesFilter(m.action, filter)).length
      count += phase.deaths.length
    }
    return count
  }, [visiblePhases, filter])

  useEffect(() => {
    containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight, behavior: 'smooth' })
  }, [totalMessages])

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/5">
        {(['all', 'discussion', 'votes', 'night'] as Filter[]).map(f => (
          <button
            key={f}
            onClick={() => onFilterChange(f)}
            className={`px-3 py-1 rounded-full text-xs font-medium capitalize transition-colors ${
              filter === f ? 'bg-white/15 text-white' : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
            }`}
          >
            {f}
          </button>
        ))}
        <span className="ml-auto text-xs text-slate-600 font-mono">{totalMessages} items</span>
      </div>

      <div ref={containerRef} className="flex-1 overflow-y-auto scrollbar-thin p-4 space-y-1">
        {visiblePhases.length === 0 && (
          <p className="text-sm text-slate-600 text-center mt-8">No phases to display</p>
        )}
        {visiblePhases.map(phase => (
          <PhaseSection key={phase.phase} phase={phase} data={data} filter={filter} />
        ))}
        {data.outcome && !activePhase && <GameEndMarker winner={data.outcome.winner} />}
      </div>
    </div>
  )
}

function PhaseSection({ phase, data, filter }: { phase: PhaseEntry; data: GameData; filter: Filter }) {
  const filteredMsgs = phase.messages.filter(m => actionMatchesFilter(m.action, filter))
  if (filteredMsgs.length === 0 && phase.deaths.length === 0 && !phase.vote_result) {
    if (filter !== 'all') return null
  }

  const isNight = phase.phase.startsWith('night')
  const phaseName = phase.phase.replace(/-/g, ' ')

  return (
    <div className="mb-2">
      <div className="flex items-center gap-3 my-3">
        <div className="flex-1 h-px bg-white/5" />
        <span className={`flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider ${
          isNight ? 'text-indigo-400' : 'text-sky-400'
        }`}>
          {isNight ? <Moon className="w-3 h-3" /> : <Sun className="w-3 h-3" />}
          {phaseName}
          {phase.living && (
            <span className="text-slate-600 font-normal lowercase">
              ({phase.living.length} alive)
            </span>
          )}
        </span>
        <div className="flex-1 h-px bg-white/5" />
      </div>

      {filteredMsgs.map((msg, i) => (
        <MessageBubble key={msg.post_id || `${msg.ts}-${i}`} msg={msg} data={data} />
      ))}

      {phase.vote_result && (filter === 'all' || filter === 'votes') && (
        <VoteResultCard result={phase.vote_result} data={data} />
      )}

      {phase.deaths.map((d, i) => (
        <DeathMarker key={`death-${d.ts}-${i}`} death={d} data={data} />
      ))}
    </div>
  )
}

function MessageBubble({ msg, data }: { msg: BotAction; data: GameData }) {
  const player = data.players[msg.sender]
  const role: Role | undefined = player?.role
  const style = role ? ROLE_STYLES[role] : undefined

  const time = new Date(msg.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  const KIND_ICONS: Record<string, React.ReactNode> = {
    'discussion-utterance': <MessageCircle className="w-3.5 h-3.5" />,
    'vote': <Vote className="w-3.5 h-3.5" />,
    'coven-target': <Target className="w-3.5 h-3.5" />,
    'investigate': <Search className="w-3.5 h-3.5" />,
    'protect': <Shield className="w-3.5 h-3.5" />,
  }

  return (
    <div className={`group animate-fade-up rounded-lg px-3 py-2 transition-colors hover:bg-white/[0.03] ${
      msg.action === 'vote' ? 'border-l-2 border-amber-500/40 bg-amber-500/5' :
      msg.action === 'coven-target' ? 'border-l-2 border-red-500/40 bg-red-500/5' :
      msg.action === 'investigate' ? 'border-l-2 border-blue-500/40 bg-blue-500/5' :
      msg.action === 'protect' ? 'border-l-2 border-purple-500/40 bg-purple-500/5' :
      ''
    }`}>
      <div className="flex items-center gap-2 mb-0.5">
        {style && <span className={`w-2 h-2 rounded-full ${style.dot} flex-shrink-0`} />}
        <span className={`text-sm font-semibold font-mono ${style?.text ?? 'text-slate-400'}`}>
          {player?.short ?? msg.sender_short ?? 'unknown'}
        </span>
        {role && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded ring-1 ring-inset ${style?.badge}`}>
            {role}
          </span>
        )}
        <span className="flex items-center gap-1 text-[10px] text-slate-600">
          {KIND_ICONS[msg.action] ?? <MessageCircle className="w-3 h-3" />}
          {msg.action.replace(/-/g, ' ')}
        </span>
        <span className="text-[10px] text-slate-700 ml-auto">{time}</span>
      </div>

      {msg.action === 'vote' ? (
        <VoteContent msg={msg} data={data} />
      ) : msg.action === 'coven-target' || msg.action === 'investigate' || msg.action === 'protect' ? (
        <NightActionContent msg={msg} data={data} />
      ) : (
        <div>
          {msg.content ? (
            <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">{msg.content}</p>
          ) : (
            <p className="text-xs text-slate-600 italic">
              (spoke during discussion)
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function VoteContent({ msg, data }: { msg: BotAction; data: GameData }) {
  const target = msg.target ? data.players[msg.target] : null
  const targetStyle = target ? ROLE_STYLES[target.role] : null
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-slate-400">votes to lynch</span>
      <span className={`font-mono font-semibold ${targetStyle?.text ?? 'text-white'}`}>
        {target?.short ?? msg.target_short ?? '?'}
      </span>
      {msg.reasoning && <span className="text-xs text-slate-500 italic ml-2">"{msg.reasoning.slice(0, 100)}"</span>}
    </div>
  )
}

function NightActionContent({ msg, data }: { msg: BotAction; data: GameData }) {
  const target = msg.target ? data.players[msg.target] : null
  const verb = msg.action === 'coven-target' ? 'targets' : msg.action === 'investigate' ? 'investigates' : 'protects'
  return (
    <div className="text-sm space-y-1">
      <div className="flex items-center gap-2">
        <span className="text-slate-400">{verb}</span>
        <span className="font-mono font-semibold text-white">{target?.short ?? msg.target_short ?? '?'}</span>
      </div>
      {msg.reasoning && (
        <p className="text-xs text-slate-500 italic leading-relaxed">"{msg.reasoning}"</p>
      )}
    </div>
  )
}

function VoteResultCard({ result, data }: { result: VoteResult; data: GameData }) {
  if (!result.tally) return null
  const sortedTally = Object.entries(result.tally).sort((a, b) => b[1] - a[1])
  const lynched = result.lynched ? data.players[result.lynched] : null

  return (
    <div className="my-3 px-3 py-2 rounded-lg bg-amber-500/5 border border-amber-500/10">
      <div className="flex items-center gap-2 text-xs text-amber-400 mb-2">
        <Vote className="w-3.5 h-3.5" />
        <span className="font-semibold uppercase tracking-wide">Vote Results</span>
      </div>
      <div className="grid grid-cols-2 gap-1">
        {sortedTally.map(([pid, votes]) => {
          const p = data.players[pid]
          const s = p ? ROLE_STYLES[p.role] : null
          return (
            <div key={pid} className="flex items-center gap-2 text-sm">
              <span className={`font-mono ${s?.text ?? 'text-slate-400'}`}>{p?.short ?? pid.slice(-8)}</span>
              <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${s?.dot ?? 'bg-slate-500'}`}
                  style={{ width: `${Math.min(100, (votes / 7) * 100)}%` }}
                />
              </div>
              <span className="text-xs text-slate-500 w-4 text-right">{votes}</span>
            </div>
          )
        })}
      </div>
      {lynched && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-red-400">
          <Skull className="w-3 h-3" />
          <span className="font-mono">{lynched.short}</span>
          <span>({lynched.role}) was lynched</span>
        </div>
      )}
    </div>
  )
}

function DeathMarker({ death, data }: { death: DeathEvent; data: GameData }) {
  const target = death.target ? data.players[death.target] : null
  return (
    <div className="my-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/5 border border-red-500/10 animate-fade-up">
      <Skull className="w-4 h-4 text-red-400" />
      <span className="text-sm text-red-300">
        <span className="font-mono font-semibold">{target?.short ?? death.target_short ?? '?'}</span>
        {target && <span className="text-red-400/70"> ({target.role})</span>}
        <span className="text-red-400/50"> was killed ({death.cause})</span>
      </span>
    </div>
  )
}

function GameEndMarker({ winner }: { winner: string }) {
  return (
    <div className="my-4 px-4 py-3 rounded-xl bg-gradient-to-r from-amber-500/10 to-amber-600/10 border border-amber-500/20 text-center">
      <Trophy className="w-6 h-6 text-amber-400 mx-auto mb-1" />
      <p className="text-lg font-bold text-amber-300">
        {winner === 'mafia' ? 'Mafia Wins!' : winner === 'draw' ? 'Draw!' : 'Villagers Win!'}
      </p>
    </div>
  )
}
