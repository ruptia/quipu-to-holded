import { useEffect, useState } from 'react'
import { api, errorMessage } from '../api'
import { RECORD_STATUS_LABELS, RECORD_STATUSES } from '../runUtils'
import type { Entity, MigrationRecordDetail, RecordPage, RecordStatus, Run } from '../types'
import { RecordStatusBadge } from './StatusBadge'

const PAGE_SIZE = 25

interface Props {
  run: Run
  entities: Entity[]
  initialStatus?: RecordStatus
}

/** Listado paginado de registros con filtros y vista del payload Quipu / Holded. */
export function RecordsTable({ run, entities, initialStatus }: Props) {
  const [entityType, setEntityType] = useState('')
  const [status, setStatus] = useState<RecordStatus | ''>(initialStatus ?? '')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<RecordPage | null>(null)
  const [selected, setSelected] = useState<MigrationRecordDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  // `run` cambia en cada sondeo mientras hay una fase en curso: refresca la tabla
  useEffect(() => {
    let cancelled = false
    api
      .records(run.id, {
        entity_type: entityType || undefined,
        status: status || undefined,
        limit: PAGE_SIZE,
        offset,
      })
      .then((p) => !cancelled && setPage(p))
      .catch((e) => !cancelled && setError(errorMessage(e)))
    return () => {
      cancelled = true
    }
  }, [run, entityType, status, offset])

  const label = (type: string) => entities.find((e) => e.type === type)?.label ?? type

  async function openRecord(recordId: number) {
    try {
      setSelected(await api.record(run.id, recordId))
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div className="records">
      <div className="filters">
        <select
          value={entityType}
          onChange={(e) => {
            setEntityType(e.target.value)
            setOffset(0)
          }}
        >
          <option value="">Todas las entidades</option>
          {run.entities.map((t) => (
            <option key={t} value={t}>
              {label(t)}
            </option>
          ))}
        </select>
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as RecordStatus | '')
            setOffset(0)
          }}
        >
          <option value="">Todos los estados</option>
          {RECORD_STATUSES.map((s) => (
            <option key={s} value={s}>
              {RECORD_STATUS_LABELS[s]}
            </option>
          ))}
        </select>
        {page && <span className="muted">{page.total} registros</span>}
      </div>

      {error && <p className="alert alert-error">{error}</p>}

      <table className="table">
        <thead>
          <tr>
            <th>Entidad</th>
            <th>Id Quipu</th>
            <th>Estado</th>
            <th>Resultado</th>
            <th>Id Holded</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {page?.items.map((r) => (
            <tr key={r.id} className="clickable" onClick={() => void openRecord(r.id)}>
              <td>{label(r.entity_type)}</td>
              <td>
                <code>{r.source_id}</code>
              </td>
              <td>
                <RecordStatusBadge status={r.status} />
              </td>
              <td className={r.summary?.includes('⚠') ? 'warning-cell' : undefined}>
                {r.summary ?? <span className="muted">—</span>}
              </td>
              <td>{r.target_id ? <code>{r.target_id}</code> : <span className="muted">—</span>}</td>
              <td className="error-cell">{r.error}</td>
            </tr>
          ))}
          {page?.items.length === 0 && (
            <tr>
              <td colSpan={6} className="muted">
                No hay registros con estos filtros.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {page && page.total > PAGE_SIZE && (
        <div className="pagination">
          <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE_SIZE)}>
            ← Anterior
          </button>
          <span className="muted">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} de {page.total}
          </span>
          <button
            disabled={offset + PAGE_SIZE >= page.total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Siguiente →
          </button>
        </div>
      )}

      {selected && (
        <div className="record-detail">
          <div className="record-detail-header">
            <strong>
              {label(selected.entity_type)} · <code>{selected.source_id}</code>
            </strong>
            <button onClick={() => setSelected(null)}>Cerrar</button>
          </div>
          <div className="payloads">
            <div>
              <h4>Quipu (origen)</h4>
              <pre>{JSON.stringify(selected.source_payload, null, 2)}</pre>
            </div>
            <div>
              <h4>Holded (destino)</h4>
              <pre>
                {selected.target_payload
                  ? JSON.stringify(selected.target_payload, null, 2)
                  : 'Sin transformar todavía'}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
