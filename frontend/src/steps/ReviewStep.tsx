import { useState } from 'react'
import { api } from '../api'
import { QuipuDocumentsImport } from '../components/QuipuDocumentsImport'
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
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const transformed = countRecords(run, 'transformed')
  const errors = countRecords(run, 'error')
  const pendingCount = countRecords(run, 'extracted') + errors

  const transform = (recordIds?: number[]) =>
    execute(async () => {
      onRun(await api.transform(run.id, recordIds))
      setSelected(new Set())
    })

  return (
    <section>
      <h2>Revisión</h2>
      <p>
        Convierte los datos de Quipu al formato de Holded <strong>sin enviar nada</strong>. Elige
        qué registros transformar, revisa el resultado de cada uno (haz clic en una fila) y, en los
        gastos, marca las líneas que son suplidos.
      </p>

      <RunHeader run={run} />
      {error && <p className="alert alert-error">{error}</p>}
      <RunCounts run={run} entities={entities} />

      <QuipuDocumentsImport
        run={run}
        entities={entities}
        busy={busy}
        onImported={() => void api.run(run.id).then(onRun)}
      />

      {errors > 0 && !busy && (
        <p className="alert alert-warning">
          {errors} registros con error no se pueden cargar. Revisa el motivo en la tabla.
        </p>
      )}

      <div className="actions">
        <button
          onClick={() => void transform([...selected])}
          disabled={busy || pending || selected.size === 0}
        >
          Transformar seleccionados ({selected.size})
        </button>
        <button onClick={() => void transform()} disabled={busy || pending || pendingCount === 0}>
          {run.status === 'transforming'
            ? 'Transformando…'
            : `Transformar pendientes (${pendingCount})`}
        </button>
        <button className="primary" onClick={onNext} disabled={busy || transformed === 0}>
          Siguiente
        </button>
      </div>

      <RecordsTable
        run={run}
        entities={entities}
        selection={{ selected, onChange: setSelected }}
        busy={busy}
        onChanged={() => void api.run(run.id).then(onRun)}
      />
    </section>
  )
}
