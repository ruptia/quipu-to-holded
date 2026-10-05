export type RunStatus =
  | 'created'
  | 'extracting'
  | 'extracted'
  | 'transforming'
  | 'transformed'
  | 'loading'
  | 'completed'
  | 'failed'

export type RecordStatus = 'extracted' | 'transformed' | 'loaded' | 'updated' | 'skipped' | 'error'

export interface ConnectionStatus {
  configured: boolean
  ok: boolean
  detail: string | null
}

export interface Connections {
  quipu: ConnectionStatus
  holded: ConnectionStatus
}

export interface Entity {
  type: string
  label: string
  depends_on: string[]
}

export interface Run {
  id: number
  status: RunStatus
  entities: string[]
  error: string | null
  created_at: string
  updated_at: string
  /** entity_type -> estado del registro -> número de registros */
  counts: Record<string, Partial<Record<RecordStatus, number>>>
}

export interface MigrationRecord {
  id: number
  entity_type: string
  source_id: string
  status: RecordStatus
  target_id: string | null
  /** Resumen de la transformación, p. ej. «Acreedor · Intracomunitario (...)» */
  summary: string | null
  error: string | null
}

export interface MigrationRecordDetail extends MigrationRecord {
  source_payload: Record<string, unknown>
  target_payload: Record<string, unknown> | null
}

export interface RecordPage {
  total: number
  items: MigrationRecord[]
}
