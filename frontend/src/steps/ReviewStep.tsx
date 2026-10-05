import { api } from '../api'
import { RecordsTable } from '../components/RecordsTable'
import { RunCounts } from '../components/RunCounts'
import { RunHeader } from '../components/RunHeader'
import { countRecords } from '../runUtils'
import type { Entity, Run } from '../types'
import { useAction } from '../useAction'

interface Props {
  run: Run
  busy: boolean
  entities: Entity[]
  onRun: (run: Run) => void
  onNext: () => void
}

export function ReviewStep({ run, busy, entities, onRun, onNext }: Props) {
  const { pending, error, execute } = useAction()
  const transformed = countRecords(run, 'transformed')
  const errors = countRecords(run, 'error')

  const transform = () => execute(async () => onRun(await api.transform(run.id)))

  return (
    <section>
      <h2>Revisión</h2>
      <p>
        Convierte los datos de Quipu al formato de Holded <strong>sin enviar nada</strong>. Revisa
        los errores y el resultado de cada registro antes de cargar.
      </p>

      <RunHeader run={run} />
      {error && <p className="alert alert-error">{error}</p>}
      <RunCounts run={run} entities={entities} />

      {errors > 0 && !busy && (
        <p className="alert alert-warning">
          {errors} registros con error no se cargarán. Corrige el mapeo y vuelve a transformar.
        </p>
      )}

      <div className="actions">
        <button onClick={() => void transform()} disabled={busy || pending}>
          {run.status === 'transforming'
            ? 'Transformando…'
            : transformed + errors > 0
              ? 'Volver a transformar'
              : 'Transformar'}
        </button>
        <button className="primary" onClick={onNext} disabled={busy || transformed === 0}>
          Siguiente
        </button>
      </div>

      <RecordsTable run={run} entities={entities} />
    </section>
  )
}
