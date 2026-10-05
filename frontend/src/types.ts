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
  /** Decisiones por registro que admite (p. ej. "supplied_lines") */
  overridable: string[]
  /** El documento original llega por fuera del API de Quipu */
  external_documents: boolean
}

export interface RecordOverrides {
  /** Índices de las líneas de Quipu que son suplidos */
  supplied_lines?: number[]
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
  overrides: RecordOverrides | null
}

export interface MigrationRecordDetail extends MigrationRecord {
  source_payload: Record<string, unknown>
  target_payload: Record<string, unknown> | null
}

export interface RecordPage {
  total: number
  items: MigrationRecord[]
}

export interface ExpenseBrief {
  record_id: number
  entity_type: string
  source_id: string
  date: string | null
  number: string | null
  issuer: string | null
  total: string | null
}

export type DocumentCheck = 'importe' | 'número' | 'emisor' | 'sin verificar' | 'no cuadra'

export interface DocumentMatch extends ExpenseBrief {
  /** Nombre original del documento */
  file: string
  method: 'número' | 'orden'
  check: DocumentCheck
}

export interface QuipuExportReport {
  matched: DocumentMatch[]
  unmatched_files: string[]
  expenses_without_file: ExpenseBrief[]
  amortizations: ExpenseBrief[]
  ignored_files: string[]
  reset_to_extracted: number
}
