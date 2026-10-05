import { useState } from 'react'
import { api, errorMessage } from '../api'
import type { MigrationRecordDetail } from '../types'

interface QuipuLine {
  attributes?: {
    concept?: string
    quantity?: string
    unitary_amount?: string
    vat_percent?: string
    deductible_vat_percent?: string
    deductible_expense_percent?: string
    kind?: string
  }
}

const KIND_LABELS: Record<string, string> = { current: 'Gasto', asset: 'Bien de inversión' }

const percent = (value?: string) => `${Number(value ?? 0).toLocaleString('es-ES')} %`
const amount = (line: QuipuLine) =>
  (Number(line.attributes?.quantity ?? 1) * Number(line.attributes?.unitary_amount ?? 0)).toLocaleString(
    'es-ES',
    { style: 'currency', currency: 'EUR' },
  )

interface Props {
  runId: number
  record: MigrationRecordDetail
  disabled: boolean
  onSaved: (record: MigrationRecordDetail) => void
}

/** Líneas del gasto en Quipu con su deducibilidad y la marca de suplido (decisión del usuario). */
export function LinesEditor({ runId, record, disabled, onSaved }: Props) {
  const lines = (record.source_payload.items as QuipuLine[] | undefined) ?? []
  const saved = record.overrides?.supplied_lines ?? []
  const [supplied, setSupplied] = useState<number[]>(saved)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const dirty = [...supplied].sort().join() !== [...saved].sort().join()

  function toggle(index: number, checked: boolean) {
    setSupplied((prev) => (checked ? [...prev, index] : prev.filter((i) => i !== index)))
  }

  async function save() {
    setPending(true)
    setError(null)
    try {
      onSaved(await api.setOverrides(runId, record.id, { supplied_lines: supplied }))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="lines-editor">
      <h4>Líneas en Quipu</h4>
      <table className="table">
        <thead>
          <tr>
            <th>Concepto</th>
            <th>Tipo</th>
            <th>Importe</th>
            <th>IVA</th>
            <th>IVA deducible</th>
            <th>Gasto deducible (IRPF)</th>
            <th>Suplido</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => (
            <tr key={index}>
              <td>{line.attributes?.concept || '—'}</td>
              <td>{KIND_LABELS[line.attributes?.kind ?? ''] ?? line.attributes?.kind}</td>
              <td>{amount(line)}</td>
              <td>{percent(line.attributes?.vat_percent)}</td>
              <td>{percent(line.attributes?.deductible_vat_percent)}</td>
              <td>{percent(line.attributes?.deductible_expense_percent)}</td>
              <td>
                <input
                  type="checkbox"
                  checked={supplied.includes(index)}
                  disabled={disabled || pending}
                  onChange={(e) => toggle(index, e.target.checked)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {error && <p className="alert alert-error">{error}</p>}
      {dirty && (
        <div className="actions">
          <span className="muted">Al guardar, el registro vuelve a «Extraído»: transfórmalo otra vez.</span>
          <button className="primary" disabled={disabled || pending} onClick={() => void save()}>
            {pending ? 'Guardando…' : 'Guardar suplidos'}
          </button>
        </div>
      )}
    </div>
  )
}
