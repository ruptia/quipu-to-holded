import { useCallback, useState } from 'react'
import { errorMessage } from './api'

/** Estado de una acción asíncrona disparada por el usuario (botones). */
export function useAction() {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const execute = useCallback(async (action: () => Promise<void>) => {
    setPending(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }, [])

  return { pending, error, execute }
}
