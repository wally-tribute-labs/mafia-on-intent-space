import { Loader2 } from 'lucide-react'

export function WaitingScreen() {
  return (
    <div className="flex items-center justify-center h-screen">
      <div className="text-center space-y-4">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-red-500/20 to-purple-600/20 border border-white/5 flex items-center justify-center mx-auto">
          <Loader2 className="w-8 h-8 text-slate-400 animate-spin" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-white">Waiting for Game</h2>
          <p className="text-sm text-slate-500 mt-1">
            The dashboard will update automatically when the host finishes provisioning spaces.
          </p>
        </div>
        <div className="flex items-center gap-2 justify-center text-xs text-slate-600">
          <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
          Polling every 5s
        </div>
      </div>
    </div>
  )
}
