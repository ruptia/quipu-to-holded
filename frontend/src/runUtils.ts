import type { RecordStatus, Run, RunStatus } from './types'

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  created: 'Creada',
  extracting: 'Extrayendo…',
  extracted: 'Extraída',
  transforming: 'Transformando…',
  transformed: 'Transformada',
  loading: 'Cargando en Holded…',
  completed: 'Completada',
  failed: 'Fallida',
}

export const RECORD_STATUS_LABELS: Record<RecordStatus, string> = {
  extracted: 'Extraído',
  transformed: 'Listo para cargar',
  loaded: 'Cargado',
  updated: 'Actualizado',
  skipped: 'Ya migrado',
  error: 'Error',
}

export const RECORD_STATUSES = Object.keys(RECORD_STATUS_LABELS) as RecordStatus[]

const BUSY: RunStatus[] = ['extracting', 'transforming', 'loading']

export const isBusy = (status: RunStatus) => BUSY.includes(status)

/** Número de registros de la ejecución en un estado (o en total si no se indica). */
export function countRecords(run: Run, status?: RecordStatus): number {
  let total = 0
  for (const byStatus of Object.values(run.counts)) {
    for (const [s, n] of Object.entries(byStatus)) {
      if (!status || s === status) total += n ?? 0
    }
  }
  return total
}
