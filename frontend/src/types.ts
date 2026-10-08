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

export interface TaxQuarter {
  quarter: number
  m303: {
    output_by_rate: { rate: string; base: string; vat: string }[]
    output_vat: string
    no_vat_eu: string
    no_vat_other: string
    input_current_base: string
    input_current_vat: string
    input_assets_base: string
    input_assets_vat: string
    input_vat: string
    result: string
  }
  /** Acumulado desde enero */
  m130: {
    income: string
    expenses: string
    net_before: string
    /** 5 % de gastos de difícil justificación (estimación directa simplificada) */
    hard_to_justify: string
    net: string
    tax_20: string
    retentions: string
    previous_payments: string
    to_pay: string
  }
  eu_purchases: string
  eu_sales: string
  purchase_retentions: string
}

export interface TaxReport {
  year: number
  years: number[]
  simplified: boolean
  quarters: TaxQuarter[]
  indicators: {
    m303: boolean
    m349: boolean
    m111: boolean
    m347: { side: 'ventas' | 'compras'; tax_id: string; name: string | null; total: string }[]
  }
}

export type QuotaStatus = 'creada' | 'manual' | 'vencida' | 'futura'

export interface Quota {
  number: number
  date: string
  amount: string
  status: QuotaStatus
  holded_entry_id: string | null
}

export interface Asset {
  id: number
  name: string
  account_code: string
  acquisition_date: string
  cost: string
  annual_rate: string
  residual_value: string
  quipu_ref: string | null
  monthly_amount: string
  expense_account: number
  accumulated_account: number
  /** Ya hay cuotas en Holded: el cuadro no se puede cambiar */
  locked: boolean
  quotas: Quota[]
  amortized: string
  due: string
  remaining: string
}

export interface AssetInput {
  name: string
  account_code: string
  acquisition_date: string
  cost: string
  annual_rate: string
  residual_value: string
}

export interface EntriesReport {
  asset: Asset
  results: { number: number; ok: boolean; detail: string }[]
}
