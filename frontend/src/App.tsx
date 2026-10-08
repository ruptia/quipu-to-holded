import { useEffect, useState } from 'react'
import { api, errorMessage } from './api'
import { Stepper } from './components/Stepper'
import { ConnectionsStep } from './steps/ConnectionsStep'
import { ExtractStep } from './steps/ExtractStep'
import { LoadStep } from './steps/LoadStep'
import { ReviewStep } from './steps/ReviewStep'
import { SummaryStep } from './steps/SummaryStep'
import { TaxReportPage } from './TaxReportPage'
import { AssetsPage } from './AssetsPage'
import type { Entity, Run } from './types'
import { useRun } from './useRun'

const STEPS = [
  { title: 'Conexiones' },
  { title: 'Extracción' },
  { title: 'Revisión' },
  { title: 'Carga' },
  { title: 'Resumen' },
] as const

export default function App() {
  const [view, setView] = useState<'wizard' | 'taxes' | 'assets'>('wizard')
  const [step, setStep] = useState(0)
  const [runId, setRunId] = useState<number | null>(null)
  const [entities, setEntities] = useState<Entity[] | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const { run, setRun, busy, error: runError } = useRun(runId)

  useEffect(() => {
    api.entities().then(setEntities, (e) => setApiError(errorMessage(e)))
  }, [])

  const handleRun = (updated: Run) => {
    setRunId(updated.id)
    setRun(updated)
  }
  const reset = () => {
    setRunId(null)
    setStep(1)
  }
  const next = () => setStep((s) => Math.min(s + 1, STEPS.length - 1))

  return (
    <div className="app">
      <header className="app-header">
        <h1>
          Quipu <span className="arrow">→</span> Holded
        </h1>
        <p className="muted">Asistente de migración</p>
        <nav className="views">
          <button className={view === 'wizard' ? 'active' : ''} onClick={() => setView('wizard')}>
            Asistente
          </button>
          <button className={view === 'taxes' ? 'active' : ''} onClick={() => setView('taxes')}>
            Impuestos
          </button>
          <button className={view === 'assets' ? 'active' : ''} onClick={() => setView('assets')}>
            Amortizaciones
          </button>
        </nav>
      </header>

      {view === 'taxes' && (
        <main className="panel">
          <TaxReportPage />
        </main>
      )}
      {view === 'assets' && (
        <main className="panel">
          <AssetsPage />
        </main>
      )}

      {view === 'wizard' && (
        <Stepper
          steps={STEPS}
          current={step}
          canOpen={(i) => i <= 1 || run != null}
          onSelect={setStep}
        />
      )}

      <main className="panel" hidden={view !== 'wizard'}>
        {apiError && (
          <p className="alert alert-error">No se puede contactar con el API: {apiError}</p>
        )}
        {runError && <p className="alert alert-error">{runError}</p>}

        {entities && step === 0 && <ConnectionsStep onNext={next} />}
        {entities && step === 1 && (
          <ExtractStep
            run={run}
            busy={busy}
            entities={entities}
            onRun={handleRun}
            onReset={reset}
            onNext={next}
          />
        )}
        {entities && run && step === 2 && (
          <ReviewStep run={run} busy={busy} entities={entities} onRun={handleRun} onNext={next} />
        )}
        {entities && run && step === 3 && (
          <LoadStep run={run} busy={busy} entities={entities} onRun={handleRun} onNext={next} />
        )}
        {entities && run && step === 4 && (
          <SummaryStep run={run} entities={entities} onReset={reset} />
        )}
        {!entities && !apiError && <p className="muted">Cargando…</p>}
      </main>
    </div>
  )
}
