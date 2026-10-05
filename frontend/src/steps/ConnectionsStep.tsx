import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Connections, ConnectionStatus } from '../types'
import { useAction } from '../useAction'

export function ConnectionsStep({ onNext }: { onNext: () => void }) {
  const [connections, setConnections] = useState<Connections | null>(null)
  const { pending, error, execute } = useAction()

  const check = useCallback(
    () => execute(async () => setConnections(await api.connections())),
    [execute],
  )

  useEffect(() => {
    void check()
  }, [check])

  return (
    <section>
      <h2>Conexiones</h2>
      <p>
        Comprobamos que las credenciales de Quipu y Holded definidas en <code>.env</code>{' '}
        funcionan.
      </p>

      {error && <p className="alert alert-error">{error}</p>}

      <div className="cards">
        <ConnectionCard
          name="Quipu"
          role="Origen: de aquí se extraen los datos"
          status={connections?.quipu}
        />
        <ConnectionCard
          name="Holded"
          role="Destino: aquí se cargan los datos"
          status={connections?.holded}
        />
      </div>

      {connections && !connections.quipu.ok && (
        <p className="alert alert-warning">
          Sin conexión con Quipu no podrás extraer datos. Revisa <code>.env</code> y aplica los
          cambios con <code>docker compose up -d</code>.
        </p>
      )}

      <div className="actions">
        <button onClick={() => void check()} disabled={pending}>
          {pending ? 'Comprobando…' : 'Volver a comprobar'}
        </button>
        <button className="primary" onClick={onNext}>
          Siguiente
        </button>
      </div>
    </section>
  )
}

const STATE_TEXT = {
  pending: 'Comprobando…',
  ok: 'Conectado',
  error: 'Error de conexión',
  missing: 'Sin configurar',
}

function ConnectionCard(props: { name: string; role: string; status?: ConnectionStatus }) {
  const { name, role, status } = props
  const state = !status ? 'pending' : status.ok ? 'ok' : status.configured ? 'error' : 'missing'
  return (
    <div className={`card card-${state}`}>
      <h3>{name}</h3>
      <p className="muted">{role}</p>
      <p>
        <strong>{STATE_TEXT[state]}</strong>
      </p>
      {status?.detail && <p className="detail">{status.detail}</p>}
    </div>
  )
}
