import type { Run } from '../types'
import { RunStatusBadge } from './StatusBadge'

export function RunHeader({ run }: { run: Run }) {
  return (
    <>
      <div className="run-header">
        <strong>Ejecución #{run.id}</strong>
        <RunStatusBadge status={run.status} />
        <span className="muted">{new Date(run.created_at).toLocaleString()}</span>
      </div>
      {run.error && <p className="alert alert-error">{run.error}</p>}
    </>
  )
}
