import type {
  Connections,
  Entity,
  MigrationRecordDetail,
  RecordPage,
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
  transform: (id: number) => post<Run>(`/runs/${id}/transform`),
  load: (id: number) => post<Run>(`/runs/${id}/load`),

  records: (id: number, filters: RecordFilters = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') params.set(key, String(value))
    }
    return request<RecordPage>(`/runs/${id}/records?${params}`)
  },
  record: (runId: number, recordId: number) =>
    request<MigrationRecordDetail>(`/runs/${runId}/records/${recordId}`),
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
