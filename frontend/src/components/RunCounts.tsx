import { RECORD_STATUS_LABELS, RECORD_STATUSES } from '../runUtils'
import type { Entity, Run } from '../types'

/** Tabla entidad × estado con el número de registros. */
export function RunCounts({ run, entities }: { run: Run; entities: Entity[] }) {
  const label = (type: string) => entities.find((e) => e.type === type)?.label ?? type

  return (
    <table className="table counts">
      <thead>
        <tr>
          <th>Entidad</th>
          {RECORD_STATUSES.map((s) => (
            <th key={s}>{RECORD_STATUS_LABELS[s]}</th>
          ))}
          <th>Total</th>
        </tr>
      </thead>
      <tbody>
        {run.entities.map((type) => {
          const byStatus = run.counts[type] ?? {}
          const total = Object.values(byStatus).reduce((sum, n) => sum + (n ?? 0), 0)
          return (
            <tr key={type}>
              <td>{label(type)}</td>
              {RECORD_STATUSES.map((s) => (
                <td key={s} className={byStatus[s] ? `count-${s}` : 'muted'}>
                  {byStatus[s] ?? 0}
                </td>
              ))}
              <td>
                <strong>{total}</strong>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
