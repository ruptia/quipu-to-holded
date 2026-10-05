import { useEffect, useState } from 'react'
import { api } from '../api'
import { RunCounts } from '../components/RunCounts'
import { RunHeader } from '../components/RunHeader'
import { RunStatusBadge } from '../components/StatusBadge'
import { countRecords } from '../runUtils'
import type { Entity, Run } from '../types'
import { useAction } from '../useAction'

interface Props {
  run: Run | null
  busy: boolean
  entities: Entity[]
  onRun: (run: Run) => void
  onReset: () => void
  onNext: () => void
}

export function ExtractStep({ run, busy, entities, onRun, onReset, onNext }: Props) {
  const { pending, error, execute } = useAction()

  const start = (selected: string[]) =>
    execute(async () => {
      const created = await api.createRun(selected)
      onRun(created)
      onRun(await api.extract(created.id))
    })

  const reExtract = (current: Run) => execute(async () => onRun(await api.extract(current.id)))

  return (
    <section>
      <h2>Extracción desde Quipu</h2>
      <p>
        Se descargan los datos de Quipu y se guardan en la base de datos local. Todavía no se envía
        nada a Holded.
      </p>

      {error && <p className="alert alert-error">{error}</p>}

      {!run ? (
        <NewRunForm entities={entities} pending={pending} onStart={start} onResume={onRun} />
      ) : (
        <>
          <RunHeader run={run} />
          <RunCounts run={run} entities={entities} />
          {busy && <p className="muted">Extrayendo datos de Quipu…</p>}
          <div className="actions">
            <button onClick={onReset} disabled={busy}>
              Nueva ejecución
            </button>
            <button onClick={() => void reExtract(run)} disabled={busy || pending}>
              Volver a extraer
            </button>
            <button
              className="primary"
              onClick={onNext}
              disabled={busy || countRecords(run) === 0}
            >
              Siguiente
            </button>
          </div>
        </>
      )}
    </section>
  )
}

interface NewRunFormProps {
  entities: Entity[]
  pending: boolean
  onStart: (selected: string[]) => void
  onResume: (run: Run) => void
}

function NewRunForm({ entities, pending, onStart, onResume }: NewRunFormProps) {
  const [selected, setSelected] = useState(() => entities.map((e) => e.type))
  const [previous, setPrevious] = useState<Run[]>([])

  useEffect(() => {
    api.runs().then(setPrevious, () => setPrevious([]))
  }, [])

  const label = (type: string) => entities.find((e) => e.type === type)?.label ?? type

  function toggle(entity: Entity, checked: boolean) {
    const next = new Set(selected)
    if (checked) {
      next.add(entity.type)
      entity.depends_on.forEach((dep) => next.add(dep))
    } else {
      next.delete(entity.type)
    }
    setSelected(entities.map((e) => e.type).filter((t) => next.has(t)))
  }

  return (
    <>
      <h3>¿Qué quieres migrar?</h3>
      <ul className="checklist">
        {entities.map((entity) => (
          <li key={entity.type}>
            <label>
              <input
                type="checkbox"
                checked={selected.includes(entity.type)}
                onChange={(e) => toggle(entity, e.target.checked)}
              />
              {entity.label}
            </label>
            {entity.depends_on.length > 0 && (
              <span className="muted">
                {' '}
                · necesita {entity.depends_on.map(label).join(', ')} migrados (en esta ejecución o
                en una anterior)
              </span>
            )}
          </li>
        ))}
      </ul>
      <div className="actions">
        <button
          className="primary"
          disabled={pending || selected.length === 0}
          onClick={() => onStart(selected)}
        >
          {pending ? 'Iniciando…' : 'Crear ejecución y extraer'}
        </button>
      </div>

      {previous.length > 0 && (
        <>
          <h3>Ejecuciones anteriores</h3>
          <table className="table">
            <tbody>
              {previous.map((r) => (
                <tr key={r.id}>
                  <td>#{r.id}</td>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                  <td>{r.entities.map(label).join(', ')}</td>
                  <td>
                    <RunStatusBadge status={r.status} />
                  </td>
                  <td className="right">
                    <button onClick={() => onResume(r)}>Reanudar</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  )
}
