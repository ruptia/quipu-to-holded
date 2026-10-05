import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage } from './api'
import { isBusy } from './runUtils'
import type { Run } from './types'

const POLL_MS = 1500

/** Carga una ejecución y la refresca periódicamente mientras tiene una fase en curso. */
export function useRun(runId: number | null) {
  const [run, setRun] = useState<Run | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (runId == null) return
    try {
      setRun(await api.run(runId))
      setError(null)
    } catch (e) {
      setError(errorMessage(e))
    }
  }, [runId])

  useEffect(() => {
    if (runId == null) setRun(null)
    else void refresh()
  }, [runId, refresh])

  const busy = run != null && isBusy(run.status)

  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => void refresh(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [busy, refresh])

  return { run, setRun, busy, error }
}
