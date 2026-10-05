import { RECORD_STATUS_LABELS, RUN_STATUS_LABELS } from '../runUtils'
import type { RecordStatus, RunStatus } from '../types'

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return <span className={`badge badge-${status}`}>{RUN_STATUS_LABELS[status]}</span>
}

export function RecordStatusBadge({ status }: { status: RecordStatus }) {
  return <span className={`badge badge-${status}`}>{RECORD_STATUS_LABELS[status]}</span>
}
