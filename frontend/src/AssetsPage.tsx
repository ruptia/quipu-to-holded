import { type ChangeEvent, useEffect, useState } from 'react'
import { api } from './api'
import type { Asset, AssetInput, EntriesReport, QuotaStatus } from './types'
import { useAction } from './useAction'

const money = (value: string | number) =>
  Number(value).toLocaleString('es-ES', { style: 'currency', currency: 'EUR' })

const STATUS_LABELS: Record<QuotaStatus, string> = {
  creada: 'En Holded',
  manual: 'Hecha a mano',
  vencida: 'Pendiente',
  futura: 'Futura',
}
const STATUS_BADGE: Record<QuotaStatus, string> = {
  creada: 'badge-loaded',
  manual: 'badge-loaded',
  vencida: 'badge-transformed',
  futura: '',
}

const EMPTY: AssetInput = {
  name: '',
  account_code: '21700000',
  acquisition_date: new Date().toISOString().slice(0, 10),
  cost: '',
  annual_rate: '26',
  residual_value: '0',
}

function AssetForm(props: {
  initial: AssetInput
  submitLabel: string
  locked?: boolean
  onSubmit: (input: AssetInput) => Promise<void>
  onCancel: () => void
}) {
  const { initial, submitLabel, locked, onSubmit, onCancel } = props
  const [input, setInput] = useState(initial)
  const { pending, error, execute } = useAction()
  const field = (key: keyof AssetInput) => ({
    value: input[key],
    onChange: (e: ChangeEvent<HTMLInputElement>) => setInput({ ...input, [key]: e.target.value }),
  })

  return (
    <form
      className="asset-form"
      onSubmit={(e) => {
        e.preventDefault()
        void execute(() => onSubmit(input))
      }}
    >
      <label>
        Nombre
        <input required {...field('name')} />
      </label>
      <label>
        Cuenta de inmovilizado
        <input required pattern="2[01][0-9]{6}" disabled={locked} {...field('account_code')} />
      </label>
      <label>
        Fecha de alta
        <input required type="date" disabled={locked} {...field('acquisition_date')} />
      </label>
      <label>
        Coste amortizable (€)
        <input required type="number" step="0.01" min="0.01" disabled={locked} {...field('cost')} />
      </label>
      <label>
        Coeficiente anual (%)
        <input required type="number" step="0.01" min="0.01" max="100" disabled={locked} {...field('annual_rate')} />
      </label>
      <label>
        Valor residual (€)
        <input type="number" step="0.01" min="0" disabled={locked} {...field('residual_value')} />
      </label>
      {locked && (
        <p className="muted">Ya hay cuotas en Holded: solo se puede cambiar el nombre.</p>
      )}
      {error && <p className="alert alert-error">{error}</p>}
      <div className="actions left">
        <button className="primary" disabled={pending}>
          {submitLabel}
        </button>
        <button type="button" onClick={onCancel}>
          Cancelar
        </button>
      </div>
    </form>
  )
}

function AssetCard({ asset, onChange }: { asset: Asset; onChange: () => void }) {
  const due = asset.quotas.filter((q) => q.status === 'vencida').map((q) => q.number)
  const [selected, setSelected] = useState<number[]>(due)
  const [editing, setEditing] = useState(false)
  const [report, setReport] = useState<EntriesReport | null>(null)
  const { pending, error, execute } = useAction()

  useEffect(() => setSelected(asset.quotas.filter((q) => q.status === 'vencida').map((q) => q.number)), [asset])

  const total = asset.quotas
    .filter((q) => selected.includes(q.number))
    .reduce((sum, q) => sum + Number(q.amount), 0)

  function createEntries() {
    const ok = window.confirm(
      `Se van a crear ${selected.length} asientos de amortización en Holded por ${money(total)} ` +
        `(${asset.expense_account} a ${asset.accumulated_account}). ¿Continuar?`,
    )
    if (!ok) return
    void execute(async () => {
      setReport(await api.createEntries(asset.id, selected))
      onChange()
    })
  }

  const toggleManual = (number: number, manual: boolean) =>
    execute(async () => {
      await api.markManual(asset.id, number, manual)
      onChange()
    })

  const input: AssetInput = {
    name: asset.name,
    account_code: asset.account_code,
    acquisition_date: asset.acquisition_date,
    cost: asset.cost,
    annual_rate: asset.annual_rate,
    residual_value: asset.residual_value,
  }

  return (
    <div className="box">
      {editing ? (
        <AssetForm
          initial={input}
          submitLabel="Guardar"
          locked={asset.locked}
          onSubmit={async (changed) => {
            await api.updateAsset(asset.id, changed)
            setEditing(false)
            onChange()
          }}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <>
          <div className="run-header">
            <h3>{asset.name}</h3>
            {asset.quipu_ref && <span className="badge">de Quipu</span>}
            <button onClick={() => setEditing(true)}>Editar</button>
            {!asset.locked && (
              <button
                onClick={() =>
                  window.confirm(`¿Borrar «${asset.name}» de la herramienta?`) &&
                  void execute(async () => {
                    await api.deleteAsset(asset.id)
                    onChange()
                  })
                }
              >
                Borrar
              </button>
            )}
          </div>
          <p className="muted">
            Cuenta {asset.account_code} · alta {asset.acquisition_date} · coste {money(asset.cost)}{' '}
            · {Number(asset.annual_rate).toLocaleString('es-ES')} % anual · cuota mensual{' '}
            <strong>{money(asset.monthly_amount)}</strong> · asiento {asset.expense_account} (debe) /{' '}
            {asset.accumulated_account} (haber)
          </p>
        </>
      )}

      <div className="stats">
        <div className="stat count-loaded">
          <span className="stat-value">{money(asset.amortized)}</span>
          <span className="muted">Amortizado en Holded</span>
        </div>
        <div className="stat count-transformed">
          <span className="stat-value">{money(asset.due)}</span>
          <span className="muted">Vencido sin registrar</span>
        </div>
        <div className="stat">
          <span className="stat-value">{money(asset.remaining)}</span>
          <span className="muted">Pendiente de amortizar</span>
        </div>
      </div>

      {error && <p className="alert alert-error">{error}</p>}
      {report && report.results.some((r) => !r.ok) && (
        <div className="alert alert-warning">
          {report.results
            .filter((r) => !r.ok)
            .map((r) => (
              <p key={r.number}>
                Cuota {r.number}: {r.detail}
              </p>
            ))}
        </div>
      )}

      <div className="actions left">
        <button className="primary" disabled={pending || selected.length === 0} onClick={createEntries}>
          {pending ? 'Creando…' : `Crear ${selected.length} asientos en Holded (${money(total)})`}
        </button>
      </div>

      <div className="scroll">
        <table className="table counts">
          <thead>
            <tr>
              <th></th>
              <th>Cuota</th>
              <th>Fecha</th>
              <th>Importe</th>
              <th>Estado</th>
              <th>Hecha a mano en Holded</th>
            </tr>
          </thead>
          <tbody>
            {asset.quotas.map((q) => (
              <tr key={q.number} className={q.status === 'futura' ? 'muted' : undefined}>
                <td>
                  {q.status === 'vencida' && (
                    <input
                      type="checkbox"
                      checked={selected.includes(q.number)}
                      onChange={(e) =>
                        setSelected((prev) =>
                          e.target.checked ? [...prev, q.number] : prev.filter((n) => n !== q.number),
                        )
                      }
                    />
                  )}
                </td>
                <td>
                  {q.number}/{asset.quotas.length}
                </td>
                <td>{q.date}</td>
                <td>{money(q.amount)}</td>
                <td>
                  <span className={`badge ${STATUS_BADGE[q.status]}`}>{STATUS_LABELS[q.status]}</span>
                </td>
                <td>
                  {(q.status === 'vencida' || q.status === 'manual') && (
                    <input
                      type="checkbox"
                      checked={q.status === 'manual'}
                      disabled={pending}
                      onChange={(e) => void toggleManual(q.number, e.target.checked)}
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Activos y cuadros de amortización, con los asientos en Holded (sin el módulo de activos). */
export function AssetsPage() {
  const [assets, setAssets] = useState<Asset[] | null>(null)
  const [adding, setAdding] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const { pending, error, execute } = useAction()

  const refresh = () => api.assets().then(setAssets, () => setAssets([]))
  useEffect(() => {
    void refresh()
  }, [])

  const importFromQuipu = () =>
    execute(async () => {
      const created = await api.importAssetsFromQuipu()
      setNotice(
        created.length
          ? `Importados: ${created.map((a) => a.name).join(', ')}`
          : 'No hay bienes de inversión nuevos en lo extraído de Quipu.',
      )
      await refresh()
    })

  return (
    <section>
      <h2>Amortizaciones</h2>
      <p className="muted">
        Cuadro de amortización lineal mensual de cada activo y sus asientos en Holded
        (681 amortización / 281x amortización acumulada), sin el módulo de activos. En estimación
        directa simplificada el coeficiente máximo de los equipos informáticos es el 26 % (tabla
        simplificada), el doble (52 %) si aplicas la amortización acelerada de empresa de reducida
        dimensión. Cada asiento se crea una sola vez.
      </p>
      <div className="actions left">
        <button onClick={() => void importFromQuipu()} disabled={pending}>
          Importar bienes de inversión de Quipu
        </button>
        <button onClick={() => setAdding(true)} disabled={adding}>
          Añadir activo
        </button>
      </div>
      {notice && <p className="muted">{notice}</p>}
      {error && <p className="alert alert-error">{error}</p>}

      {adding && (
        <div className="box">
          <h3>Nuevo activo</h3>
          <AssetForm
            initial={EMPTY}
            submitLabel="Añadir"
            onSubmit={async (input) => {
              await api.createAsset(input)
              setAdding(false)
              await refresh()
            }}
            onCancel={() => setAdding(false)}
          />
        </div>
      )}

      {assets === null && <p className="muted">Cargando…</p>}
      {assets?.length === 0 && !adding && (
        <p className="muted">No hay activos. Impórtalos de Quipu o añádelos a mano.</p>
      )}
      {assets?.map((asset) => (
        <AssetCard key={asset.id} asset={asset} onChange={() => void refresh()} />
      ))}
    </section>
  )
}

