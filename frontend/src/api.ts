import type {
  Connections,
  Entity,
  MigrationRecordDetail,
  QuipuExportReport,
  RecordPage,
  RecordOverrides,
  RecordStatus,
  Run,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(formatDetail(body?.detail) ?? `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

/** FastAPI devuelve `detail` como texto o como lista de errores de validación. */
function formatDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg).join('; ')
  return null
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export interface RecordFilters {
  entity_type?: string
  status?: RecordStatus
  limit?: number
  offset?: number
}

export const api = {
  connections: () => request<Connections>('/connections'),
  entities: () => request<Entity[]>('/entities'),

  runs: () => request<Run[]>('/runs'),
  run: (id: number) => request<Run>(`/runs/${id}`),
  createRun: (entities: string[]) => post<Run>('/runs', { entities }),
  extract: (id: number) => post<Run>(`/runs/${id}/extract`),
  /** Sin `recordIds`, los pendientes; con ellos, exactamente esos registros. */
  transform: (id: number, recordIds?: number[]) =>
    post<Run>(`/runs/${id}/transform`, recordIds ? { record_ids: recordIds } : undefined),
  /** Envía a Holded los registros transformados indicados. */
  load: (id: number, recordIds: number[]) =>
    post<Run>(`/runs/${id}/load`, { record_ids: recordIds }),

  records: (id: number, filters: RecordFilters = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') params.set(key, String(value))
    }
    return request<RecordPage>(`/runs/${id}/records?${params}`)
  },
  record: (runId: number, recordId: number) =>
    request<MigrationRecordDetail>(`/runs/${runId}/records/${recordId}`),
  setOverrides: (runId: number, recordId: number, overrides: RecordOverrides) =>
    request<MigrationRecordDetail>(`/runs/${runId}/records/${recordId}/overrides`, {
      method: 'PUT',
      body: JSON.stringify(overrides),
    }),

  /** Sube los documentos del exportador de Quipu (ficheros o ZIP) y los empareja con los gastos. */
  async importQuipuDocuments(runId: number, files: File[]): Promise<QuipuExportReport> {
    const form = new FormData()
    files.forEach((file) => form.append('files', file, file.name))
    const response = await fetch(`/api/runs/${runId}/documents/quipu-export`, {
      method: 'POST',
      body: form, // sin Content-Type: el navegador pone el boundary del multipart
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(formatDetail(body?.detail) ?? `${response.status} ${response.statusText}`)
    }
    return response.json() as Promise<QuipuExportReport>
  },

  /** Ids de todos los registros que cumplen el filtro (recorre las páginas). */
  async recordIds(runId: number, filters: Omit<RecordFilters, 'limit' | 'offset'> = {}) {
    const ids: number[] = []
    for (let offset = 0; ; offset += 500) {
      const page = await api.records(runId, { ...filters, limit: 500, offset })
      ids.push(...page.items.map((r) => r.id))
      if (ids.length >= page.total || page.items.length === 0) return ids
    }
  },
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
