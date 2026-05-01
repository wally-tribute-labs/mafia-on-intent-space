import { useState, useEffect, useCallback, useRef } from 'react'
import type { GameData } from './types'

export function useGameData(pollInterval = 5000) {
  const [data, setData] = useState<GameData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const lastFetch = useRef(0)

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch('/api/game')
      const json: GameData = await res.json()
      setData(json)
      setError(null)
      lastFetch.current = Date.now()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetch_() }, [fetch_])

  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(fetch_, pollInterval)
    return () => clearInterval(id)
  }, [autoRefresh, fetch_, pollInterval])

  return { data, loading, error, autoRefresh, setAutoRefresh, refresh: fetch_, lastFetch: lastFetch.current }
}
