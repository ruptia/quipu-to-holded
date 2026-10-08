import { useEffect, useState } from 'react'
import { api, errorMessage } from './api'
import type { TaxQuarter, TaxReport } from './types'

const money = (value: string) =>
  Number(value).toLocaleString('es-ES', { style: 'currency', currency: 'EUR' })

interface Row {
  label: string
  value: (q: TaxQuarter) => string
  strong?: boolean
}

function QuarterTable({ quarters, rows }: { quarters: TaxQuarter[]; rows: Row[] }) {
  return (
    <table className="table counts">
      <thead>
        <tr>
          <th>Concepto</th>
          {quarters.map((q) => (
            <th key={q.quarter}>{q.quarter}T</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label}>
            <td>{row.strong ? <strong>{row.label}</strong> : row.label}</td>
            {quarters.map((q) => {
              const value = row.value(q)
              return (
                <td key={q.quarter} className={Number(value) === 0 ? 'muted' : undefined}>
                  {row.strong ? <strong>{money(value)}</strong> : money(value)}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Cuadre orientativo del 303 y el 130 con los datos de Quipu, e indicadores de otros modelos. */
export function TaxReportPage() {
  const [report, setReport] = useState<TaxReport | null>(null)
  const [year, setYear] = useState<number | undefined>(undefined)
  const [simplified, setSimplified] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.taxReport(year, simplified).then(setReport, (e) => setError(errorMessage(e)))
  }, [year, simplified])

  if (error) return <p className="alert alert-error">{error}</p>
  if (!report) return <p className="muted">Calculando…</p>

  const quarters = report.quarters
  const rates = [...new Set(quarters.flatMap((q) => q.m303.output_by_rate.map((r) => r.rate)))]
  const rateRow = (rate: string, key: 'base' | 'vat') => (q: TaxQuarter) =>
    q.m303.output_by_rate.find((r) => r.rate === rate)?.[key] ?? '0'

  const rows303: Row[] = [
    ...rates.flatMap((rate) => [
      { label: `IVA repercutido ${Number(rate)} % · base`, value: rateRow(rate, 'base') },
      { label: `IVA repercutido ${Number(rate)} % · cuota`, value: rateRow(rate, 'vat') },
    ]),
    { label: 'Total IVA repercutido', value: (q) => q.m303.output_vat, strong: true },
    { label: 'Operaciones sin IVA con la UE · base', value: (q) => q.m303.no_vat_eu },
    { label: 'Operaciones sin IVA fuera de la UE · base', value: (q) => q.m303.no_vat_other },
    { label: 'IVA deducible corriente · base', value: (q) => q.m303.input_current_base },
    { label: 'IVA deducible corriente · cuota', value: (q) => q.m303.input_current_vat },
    { label: 'IVA deducible bienes de inversión · base', value: (q) => q.m303.input_assets_base },
    { label: 'IVA deducible bienes de inversión · cuota', value: (q) => q.m303.input_assets_vat },
    { label: 'Total IVA deducible', value: (q) => q.m303.input_vat, strong: true },
    { label: 'Resultado (repercutido − deducible)', value: (q) => q.m303.result, strong: true },
  ]
  const rows130: Row[] = [
    { label: 'Ingresos (acumulado)', value: (q) => q.m130.income },
    { label: 'Gastos deducibles (acumulado)', value: (q) => q.m130.expenses },
    ...(report.simplified
      ? [
          { label: 'Rendimiento neto previo', value: (q: TaxQuarter) => q.m130.net_before },
          {
            label: 'Gastos de difícil justificación (5 %, máx. 2.000 €)',
            value: (q: TaxQuarter) => q.m130.hard_to_justify,
          },
        ]
      : []),
    { label: 'Rendimiento neto', value: (q) => q.m130.net, strong: true },
    { label: '20 % del rendimiento', value: (q) => q.m130.tax_20 },
    { label: 'Retenciones soportadas (acumulado)', value: (q) => q.m130.retentions },
    { label: 'Pagos de trimestres anteriores', value: (q) => q.m130.previous_payments },
    { label: 'A ingresar', value: (q) => q.m130.to_pay, strong: true },
  ]
  const { indicators } = report

  return (
    <section>
      <div className="run-header">
        <h2>Impuestos</h2>
        {report.years.length > 1 && (
          <select value={report.year} onChange={(e) => setYear(Number(e.target.value))}>
            {report.years.map((y) => (
              <option key={y}>{y}</option>
            ))}
          </select>
        )}
      </div>
      <p className="alert alert-warning">
        Cálculo <strong>orientativo</strong> con los datos extraídos de Quipu (facturas emitidas,
        gastos y tickets), para compararlo con lo que presentaste y con lo que calcule Holded.
        Holded solo cuenta los documentos <strong>aprobados</strong>, no los borradores.
        Confírmalo siempre con tu gestor.
      </p>

      <h3>Modelo 303 · IVA {report.year}</h3>
      <QuarterTable quarters={quarters} rows={rows303} />

      <div className="run-header">
        <h3>Modelo 130 · IRPF {report.year}</h3>
        <select value={simplified ? 's' : 'n'} onChange={(e) => setSimplified(e.target.value === 's')}>
          <option value="s">Estimación directa simplificada</option>
          <option value="n">Estimación directa normal</option>
        </select>
      </div>
      <QuarterTable quarters={quarters} rows={rows130} />
      <p className="muted">
        Gastos deducibles: base + IVA no deducible, por el % deducible de cada línea. Los bienes de
        inversión no son gasto (sí sus cuotas de amortización registradas en Quipu). En la
        simplificada se restan además los gastos de difícil justificación: 5 % del rendimiento
        neto previo, con un máximo de 2.000 € al año.
      </p>

      <h3>Otros modelos según tus datos</h3>
      <ul className="checklist">
        <li>
          <strong>390</strong> (resumen anual del IVA):{' '}
          {indicators.m303 ? 'sí, porque presentas el 303.' : 'no hay operaciones con IVA.'}
        </li>
        <li>
          <strong>349</strong> (operaciones intracomunitarias):{' '}
          {indicators.m349
            ? 'tienes compras o ventas con la UE sin IVA (' +
              quarters
                .filter((q) => Number(q.eu_purchases) || Number(q.eu_sales))
                .map((q) => `${q.quarter}T: ${money(String(Number(q.eu_purchases) + Number(q.eu_sales)))}`)
                .join(', ') +
              '). Revisa con tu gestor si debes presentarlo.'
            : 'no hay operaciones con la UE.'}
        </li>
        <li>
          <strong>347</strong> (operaciones con terceros &gt; 3.005,06 €):{' '}
          {indicators.m347.length === 0
            ? 'ningún cliente ni proveedor español lo supera.'
            : indicators.m347.map((x) => `${x.name ?? x.tax_id} (${x.side}, ${money(x.total)})`).join('; ')}
        </li>
        <li>
          <strong>111 / 115</strong> (retenciones que practicas):{' '}
          {indicators.m111
            ? 'hay facturas recibidas con retención.'
            : 'ninguna factura recibida lleva retención.'}
        </li>
      </ul>

      <h3>Configuración manual en Holded</h3>
      <p className="muted">
        Quipu no permite leer por API tus actividades ni tus modelos, y Holded no permite escribirlos:
        configúralos tú en los ajustes de Holded (datos fiscales de la empresa e impuestos).
      </p>
      <ul className="checklist">
        <li>☐ Actividades económicas: los epígrafes del IAE de tu modelo 036/037.</li>
        <li>☐ Régimen de IVA (general) y de IRPF (estimación directa normal o simplificada).</li>
        <li>☐ Modelos periódicos: 303 y 130 trimestrales, y el resto de la lista anterior.</li>
        <li>☐ Antes de aprobar las facturas emitidas importadas: «No enviar a Verifactu».</li>
        <li>
          ☐ Marcar la casilla «no deducible» en cada subcuenta «… – no deducible IRPF» (el API de
          Holded no permite marcarla).
        </li>
        <li>☐ Registrar las cuotas de los bienes de inversión en la pestaña Amortizaciones.</li>
      </ul>
    </section>
  )
}
