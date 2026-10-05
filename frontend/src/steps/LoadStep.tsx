import { useState } from 'react'
import { api } from '../api'
import { RecordsTable } from '../components/RecordsTable'
import { RunCounts } from '../components/RunCounts'
import { RunHeader } from '../components/RunHeader'
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
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const hasInvoices = run.entities.includes('invoices')

  function load() {
    const ok = window.confirm(
      `Se van a crear o actualizar ${selected.size} registros en Holded (como borrador). ` +
        'Esta acción no se puede deshacer desde aquí. ¿Continuar?',
    )
    if (!ok) return
    void execute(async () => {
      onRun(await api.load(run.id, [...selected]))
      setSelected(new Set())
    })
  }

  return (
    <section>
      <h2>Carga en Holded</h2>
      <p className="alert alert-warning">
        Esta fase escribe datos reales en Holded. <strong>Solo se envían los registros que
        selecciones.</strong> Los que ya se migraron antes se actualizan si siguen en borrador en
        Holded; los aprobados no se tocan.
      </p>
      {hasInvoices && (
        <p className="alert alert-warning">
          <strong>Verifactu:</strong> las facturas emitidas ya se registraron en la AEAT desde
          Quipu. Se crean en Holded como <strong>borrador</strong>, con su PDF adjunto, y no se
          envían. Antes de aprobar cada una en Holded, marca{' '}
          <strong>Opciones → No enviar a Verifactu</strong>.
        </p>
      )}

      <RunHeader run={run} />
      {error && <p className="alert alert-error">{error}</p>}
      <RunCounts run={run} entities={entities} />

      <div className="actions">
        <button
          className="primary"
          onClick={load}
          disabled={busy || pending || selected.size === 0}
        >
          {run.status === 'loading' ? 'Cargando…' : `Enviar ${selected.size} seleccionados a Holded`}
        </button>
        <button onClick={onNext} disabled={busy}>
          Ver resumen
        </button>
      </div>

      <h3>Listos para enviar</h3>
      <RecordsTable
        run={run}
        entities={entities}
        fixedStatus="transformed"
        selection={{ selected, onChange: setSelected }}
        busy={busy}
      />
    </section>
  )
}
