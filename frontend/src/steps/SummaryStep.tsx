import { RecordsTable } from '../components/RecordsTable'
import { RunCounts } from '../components/RunCounts'
import { RunHeader } from '../components/RunHeader'
import { countRecords } from '../runUtils'
import type { Entity, Run } from '../types'

interface Props {
  run: Run
  entities: Entity[]
  onReset: () => void
}

export function SummaryStep({ run, entities, onReset }: Props) {
  const errors = countRecords(run, 'error')
  const stats = [
    { label: 'Creados', value: countRecords(run, 'loaded'), tone: 'loaded' },
    { label: 'Actualizados', value: countRecords(run, 'updated'), tone: 'updated' },
    { label: 'Ya migrados', value: countRecords(run, 'skipped'), tone: 'skipped' },
    { label: 'Con error', value: errors, tone: 'error' },
  ]

  return (
    <section>
      <h2>Resumen</h2>
      <RunHeader run={run} />

      <div className="stats">
        {stats.map((s) => (
          <div key={s.label} className={`stat count-${s.tone}`}>
            <span className="stat-value">{s.value}</span>
            <span className="muted">{s.label}</span>
          </div>
        ))}
      </div>

      <RunCounts run={run} entities={entities} />

      {errors > 0 && (
        <>
          <h3>Registros con error</h3>
          <RecordsTable run={run} entities={entities} initialStatus="error" />
        </>
      )}

      <div className="actions">
        <button className="primary" onClick={onReset}>
          Nueva migración
        </button>
      </div>
    </section>
  )
}
