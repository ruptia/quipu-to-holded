import { api } from '../api'
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

export function LoadStep({ run, busy, entities, onRun, onNext }: Props) {
  const { pending, error, execute } = useAction()
  const pendingLoad = countRecords(run, 'transformed')

  function load() {
    const ok = window.confirm(
      `Se van a crear o actualizar hasta ${pendingLoad} registros en Holded. ` +
        'Esta acción no se puede deshacer desde aquí. ¿Continuar?',
    )
    if (ok) void execute(async () => onRun(await api.load(run.id)))
  }

  return (
    <section>
      <h2>Carga en Holded</h2>
      <p className="alert alert-warning">
        Esta fase escribe datos reales en Holded. Los contactos que ya se migraron en ejecuciones
        anteriores se actualizan con los datos de esta; el resto de registros ya migrados se
        omiten.
      </p>

      <RunHeader run={run} />
      {error && <p className="alert alert-error">{error}</p>}
      <RunCounts run={run} entities={entities} />

      <div className="actions">
        <button
          className="primary"
          onClick={load}
          disabled={busy || pending || pendingLoad === 0}
        >
          {run.status === 'loading' ? 'Cargando…' : `Enviar ${pendingLoad} registros a Holded`}
        </button>
        <button onClick={onNext} disabled={busy || run.status !== 'completed'}>
          Ver resumen
        </button>
      </div>
    </section>
  )
}
