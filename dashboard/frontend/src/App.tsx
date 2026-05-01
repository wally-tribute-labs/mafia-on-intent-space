import { useState, useCallback } from 'react'
import { useGameData } from './hooks'
import { GameHeader } from './components/GameHeader'
import { PhaseSidebar } from './components/PhaseSidebar'
import { PhaseTimeline } from './components/PhaseTimeline'
import { ChatView } from './components/ChatView'
import { WaitingScreen } from './components/WaitingScreen'
import { Loader2, AlertCircle } from 'lucide-react'

export default function App() {
  const { data, loading, error, autoRefresh, setAutoRefresh, refresh } = useGameData()
  const [activePhase, setActivePhase] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'discussion' | 'votes' | 'night' | 'system'>('all')

  const handlePhaseClick = useCallback((phase: string) => {
    setActivePhase(prev => prev === phase ? null : phase)
  }, [])

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="w-6 h-6 text-slate-500 animate-spin" />
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center space-y-2">
          <AlertCircle className="w-8 h-8 text-red-400 mx-auto" />
          <p className="text-sm text-red-300">{error}</p>
          <button onClick={refresh} className="text-xs text-slate-400 underline">Retry</button>
        </div>
      </div>
    )
  }

  if (!data || data.status === 'waiting') {
    return <WaitingScreen />
  }

  return (
    <div className="h-screen flex flex-col">
      <GameHeader
        data={data}
        autoRefresh={autoRefresh}
        onToggleRefresh={() => setAutoRefresh(v => !v)}
        onRefresh={refresh}
        loading={loading}
      />

      <div className="flex-1 flex overflow-hidden">
        <aside className="w-64 border-r border-white/5 overflow-y-auto scrollbar-thin flex-shrink-0">
          <PhaseSidebar
            data={data}
            activePhase={activePhase}
            onSelect={handlePhaseClick}
          />
        </aside>

        <main className="flex-1 flex flex-col overflow-hidden">
          <div className="px-4 border-b border-white/5">
            <PhaseTimeline data={data} onPhaseClick={handlePhaseClick} activePhase={activePhase} />
          </div>

          <ChatView
            data={data}
            filter={filter}
            onFilterChange={setFilter}
            activePhase={activePhase}
          />
        </main>
      </div>
    </div>
  )
}
