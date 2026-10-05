import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { DocumentCheck, Entity, ExpenseBrief, QuipuExportReport, Run } from '../types'
import { useAction } from '../useAction'

// Primero lo que conviene revisar
const CHECK_ORDER: Record<DocumentCheck, number> = {
  'no cuadra': 0,
  'sin verificar': 1,
  emisor: 2,
  número: 3,
  importe: 4,
}
const CHECK_BADGE: Record<DocumentCheck, string> = {
  'no cuadra': 'badge-error',
  'sin verificar': '',
  emisor: 'badge-transformed',
  número: 'badge-loaded',
  importe: 'badge-loaded',
}

const describe = (e: ExpenseBrief) =>
  [e.date, e.issuer, e.number, e.total && `${Number(e.total).toLocaleString('es-ES')} €`]
    .filter(Boolean)
    .join(' · ')

interface Props {
  run: Run
  entities: Entity[]
  busy: boolean
  onImported: () => void
}

/** Sube la descarga del exportador de Quipu y empareja cada documento con su gasto. */
export function QuipuDocumentsImport({ run, entities, busy, onImported }: Props) {
  const folderInput = useRef<HTMLInputElement>(null)
  const [report, setReport] = useState<QuipuExportReport | null>(null)
  const { pending, error, execute } = useAction()
  const relevant = run.entities.some((t) => entities.find((e) => e.type === t)?.external_documents)

  useEffect(() => {
    // Permite elegir una carpeta entera (atributo no estándar, no está en los tipos de React)
    folderInput.current?.setAttribute('webkitdirectory', '')
  }, [relevant])

  if (!relevant) return null

  function upload(list: FileList | null) {
    const files = list ? [...list] : []
    if (files.length === 0) return
    void execute(async () => {
      setReport(await api.importQuipuDocuments(run.id, files))
      onImported()
    })
  }

  const disabled = busy || pending
  const count = (check: DocumentCheck) => report?.matched.filter((m) => m.check === check).length ?? 0
  const byNumber = report?.matched.filter((m) => m.method === 'número').length ?? 0

  return (
    <div className="box">
      <h3>Documentos de los gastos</h3>
      <p className="muted">
        El API de Quipu no da el documento original de los gastos. Descárgalos con el{' '}
        <strong>exportador de Quipu</strong> y selecciona aquí la carpeta o el ZIP: cada fichero se
        empareja con su gasto (por el número de factura y por el orden de creación en Quipu) y se
        verifica con el importe del PDF.
      </p>
      <div className="actions left">
        <label className={`button${disabled ? ' disabled' : ''}`}>
          Seleccionar carpeta
          <input
            ref={folderInput}
            type="file"
            multiple
            hidden
            disabled={disabled}
            onChange={(e) => upload(e.target.files)}
          />
        </label>
        <label className={`button${disabled ? ' disabled' : ''}`}>
          Seleccionar ZIP o ficheros
          <input
            type="file"
            multiple
            hidden
            accept=".zip,.pdf,.png,.jpg,.jpeg"
            disabled={disabled}
            onChange={(e) => upload(e.target.files)}
          />
        </label>
        {pending && <span className="muted">Subiendo y emparejando…</span>}
      </div>
      {error && <p className="alert alert-error">{error}</p>}

      {report && (
        <>
          <p>
            <strong>{report.matched.length} documentos emparejados</strong> ({byNumber} por número,{' '}
            {report.matched.length - byNumber} por orden) · verificados por importe:{' '}
            {count('importe')} · por número o emisor: {count('número') + count('emisor')} · sin
            verificar (imágenes): {count('sin verificar')} · no cuadran: {count('no cuadra')}
          </p>
          {report.amortizations.length > 0 && (
            <p className="muted">
              {report.amortizations.length} cuotas de amortización sin documento (es lo esperado).
            </p>
          )}
          {report.reset_to_extracted > 0 && (
            <p className="alert alert-warning">
              {report.reset_to_extracted} registros ya transformados vuelven a «Extraído»:
              transfórmalos otra vez para que lleven su documento.
            </p>
          )}
          {(report.unmatched_files.length > 0 || report.expenses_without_file.length > 0) && (
            <div className="alert alert-warning">
              <p>Sin emparejar (revísalos y adjúntalos a mano en Holded):</p>
              <ul>
                {report.unmatched_files.map((f) => (
                  <li key={f}>Fichero: {f}</li>
                ))}
                {report.expenses_without_file.map((e) => (
                  <li key={e.record_id}>Gasto: {describe(e)}</li>
                ))}
              </ul>
            </div>
          )}
          {report.ignored_files.length > 0 && (
            <p className="muted">Ignorados: {report.ignored_files.join(', ')}</p>
          )}

          <div className="scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Documento</th>
                  <th>Gasto en Quipu</th>
                  <th>Emparejado por</th>
                  <th>Verificación</th>
                </tr>
              </thead>
              <tbody>
                {[...report.matched]
                  .sort((a, b) => CHECK_ORDER[a.check] - CHECK_ORDER[b.check])
                  .map((m) => (
                    <tr key={m.record_id}>
                      <td>{m.file}</td>
                      <td>{describe(m)}</td>
                      <td>{m.method}</td>
                      <td>
                        <span className={`badge ${CHECK_BADGE[m.check]}`}>{m.check}</span>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
