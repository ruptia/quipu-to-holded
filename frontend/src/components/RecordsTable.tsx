import { useEffect, useState } from 'react'
import { api, errorMessage } from '../api'
import { RECORD_STATUS_LABELS, RECORD_STATUSES } from '../runUtils'
import type { Entity, MigrationRecordDetail, RecordPage, RecordStatus, Run } from '../types'
import { LinesEditor } from './LinesEditor'
import { RecordStatusBadge } from './StatusBadge'

const PAGE_SIZE = 25

export interface Selection {
  selected: Set<number>
  onChange: (selected: Set<number>) => void
}

interface Props {
  run: Run
  entities: Entity[]
  initialStatus?: RecordStatus
  /** Estado fijo (sin selector), p. ej. solo «transformed» en la carga */
  fixedStatus?: RecordStatus
  /** Si se pasa, cada registro tiene una casilla para seleccionarlo */
  selection?: Selection
  /** Hay una fase en curso: no se permite editar */
  busy?: boolean
  /** Se ha cambiado algo de un registro (p. ej. sus suplidos) */
  onChanged?: () => void
}

/** Listado paginado de registros con filtros, selección y detalle (Quipu / Holded). */
export function RecordsTable(props: Props) {
  const { run, entities, initialStatus, fixedStatus, selection, busy, onChanged } = props
  const [entityType, setEntityType] = useState('')
  const [status, setStatus] = useState<RecordStatus | ''>(fixedStatus ?? initialStatus ?? '')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<RecordPage | null>(null)
  const [detail, setDetail] = useState<MigrationRecordDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const filters = { entity_type: entityType || undefined, status: status || undefined }

  // `run` cambia en cada sondeo mientras hay una fase en curso: refresca la tabla
  useEffect(() => {
    let cancelled = false
    api
      .records(run.id, { ...filters, limit: PAGE_SIZE, offset })
      .then((p) => !cancelled && setPage(p))
      .catch((e) => !cancelled && setError(errorMessage(e)))
    return () => {
      cancelled = true
    }
  }, [run, entityType, status, offset])

  const entity = (type: string) => entities.find((e) => e.type === type)
  const label = (type: string) => entity(type)?.label ?? type

  async function openRecord(recordId: number) {
    try {
      setDetail(await api.record(run.id, recordId))
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  // --- Selección ---
  const selected = selection?.selected ?? new Set<number>()
  const pageIds = page?.items.map((r) => r.id) ?? []
  const pageAllSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id))

  function setSelected(ids: number[], checked: boolean) {
    const next = new Set(selected)
    ids.forEach((id) => (checked ? next.add(id) : next.delete(id)))
    selection?.onChange(next)
  }

  async function selectAllFiltered() {
    try {
      setSelected(await api.recordIds(run.id, filters), true)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const columns = selection ? 7 : 6

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
        {!fixedStatus && (
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
        )}
        {page && <span className="muted">{page.total} registros</span>}
      </div>

      {selection && (
        <div className="selection-bar">
          <strong>{selected.size} seleccionados</strong>
          {page && page.total > 0 && (
            <button onClick={() => void selectAllFiltered()}>
              Seleccionar los {page.total} del filtro
            </button>
          )}
          {selected.size > 0 && (
            <button onClick={() => selection.onChange(new Set())}>Quitar selección</button>
          )}
        </div>
      )}

      {error && <p className="alert alert-error">{error}</p>}

      <table className="table">
        <thead>
          <tr>
            {selection && (
              <th>
                <input
                  type="checkbox"
                  aria-label="Seleccionar esta página"
                  checked={pageAllSelected}
                  onChange={(e) => setSelected(pageIds, e.target.checked)}
                />
              </th>
            )}
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
            <tr
              key={r.id}
              className={`clickable${detail?.id === r.id ? ' active-row' : ''}`}
              onClick={() => void openRecord(r.id)}
            >
              {selection && (
                <td onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    aria-label={`Seleccionar ${r.source_id}`}
                    checked={selected.has(r.id)}
                    onChange={(e) => setSelected([r.id], e.target.checked)}
                  />
                </td>
              )}
              <td>{label(r.entity_type)}</td>
              <td>
                <code>{r.source_id}</code>
              </td>
              <td>
                <RecordStatusBadge status={r.status} />
              </td>
              <td className={r.summary?.includes('⚠') ? 'warning-cell' : undefined}>
                {r.summary ?? <span className="muted">—</span>}
                {r.overrides?.supplied_lines?.length ? (
                  <span className="badge badge-transformed"> suplidos marcados</span>
                ) : null}
              </td>
              <td>{r.target_id ? <code>{r.target_id}</code> : <span className="muted">—</span>}</td>
              <td className="error-cell">{r.error}</td>
            </tr>
          ))}
          {page?.items.length === 0 && (
            <tr>
              <td colSpan={columns} className="muted">
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

      {detail && (
        <div className="record-detail">
          <div className="record-detail-header">
            <strong>
              {label(detail.entity_type)} · <code>{detail.source_id}</code>
            </strong>
            <button onClick={() => setDetail(null)}>Cerrar</button>
          </div>
          {entity(detail.entity_type)?.overridable.includes('supplied_lines') && (
            <LinesEditor
              key={`${detail.id}-${detail.status}`}
              runId={run.id}
              record={detail}
              disabled={!!busy}
              onSaved={(updated) => {
                setDetail(updated)
                onChanged?.()
              }}
            />
          )}
          <div className="payloads">
            <div>
              <h4>Quipu (origen)</h4>
              <pre>{JSON.stringify(detail.source_payload, null, 2)}</pre>
            </div>
            <div>
              <h4>Holded (destino)</h4>
              <pre>
                {detail.target_payload
                  ? JSON.stringify(detail.target_payload, null, 2)
                  : 'Sin transformar todavía'}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
