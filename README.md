# Quipu → Holded

Asistente local para migrar los datos del ERP de **Quipu** a **Holded**.

Son dos contenedores que `docker compose` levanta juntos:

| Contenedor | Imagen | Contenido |
|---|---|---|
| **backend** | `python:3.12-slim` | API con FastAPI, modelo con SQLAlchemy 2, migraciones con Alembic y el importador. SQLite en el volumen `qth-data` |
| **frontend** | `nginx:stable-alpine` | La aplicación React (Vite + TypeScript) ya compilada. nginx redirige `/api/*` al backend |

```
navegador ──► frontend (nginx :8080) ──/api/*──► backend (FastAPI :8000) ──► SQLite (volumen qth-data)
```

El navegador solo habla con el frontend, que reenvía `/api` al backend. Al ser el mismo origen, no
hace falta configurar CORS.

## Cómo funciona

Cada migración es una **ejecución** que pasa por tres fases. Todo queda guardado en SQLite, así que
cualquier fase se puede repetir:

| Fase | Qué hace | ¿Toca Holded? |
|---|---|---|
| **Extracción** | Descarga los datos de Quipu y guarda el JSON original en la tabla `records` | No |
| **Transformación** | Convierte cada registro al formato de Holded. Puedes revisar el resultado y los errores | No |
| **Carga** | Crea los registros en Holded y guarda el id que devuelve Holded | **Sí** |

La carga **nunca duplica** entre ejecuciones. Si un registro de Quipu ya se migró antes, las entidades
que lo permiten (hoy, los contactos) **se actualizan** en Holded con los datos nuevos; el resto se
marcan como «Ya migrado». Las referencias entre entidades (por ejemplo, factura → contacto) se
resuelven con esos mismos ids.

El asistente sigue los pasos: **Conexiones → Extracción → Revisión → Carga → Resumen**.

### Mapeo de contactos

| Quipu | Holded |
|---|---|
| `is_client` | `type: client` |
| `is_supplier` (o ninguno de los dos) | `type: creditor`: acreedor, cuenta 410, como en Quipu |
| Cliente y proveedor a la vez | El tipo con más volumen facturado (Holded solo admite uno) |
| País España | `taxOperation: general` |
| País de la UE con VAT válido en **VIES** | `taxOperation: intra`, y el VAT con prefijo en `vatnumber` y `code` |
| País de la UE sin VAT válido | Sin `taxOperation` y aviso ⚠ en la revisión para decidirlo a mano |
| País fuera de la UE | `taxOperation: nosujeto` |

La validación en VIES (servicio de la Comisión Europea) se hace en la fase de transformación. Si
VIES no responde, el registro queda en error y basta con volver a transformar. La columna
«Resultado» de la revisión muestra la clasificación de cada contacto.

### Facturas emitidas (Verifactu)

Las facturas emitidas de Quipu **ya están registradas en la AEAT** (Verifactu). Por eso:

- En Holded se crean **siempre como borrador** (`approveDoc: false`). Si se aprobaran desde el API,
  Holded las enviaría otra vez a Verifactu.
- Antes de aprobarlas en Holded hay que marcar **Opciones → No enviar a Verifactu** en cada una.
- Se adjunta el **PDF original de Quipu**, que lleva el QR de Verifactu.

| Quipu | Holded |
|---|---|
| `number`, `issue_date`, primera de `due_dates`, `notes` | `invoiceNum`, `date`, `dueDate`, `notes` |
| Contacto | `contactId` del contacto ya migrado |
| Líneas (`include=items`): concepto, descripción, cantidad, precio, % descuento | `items`: `name`, `desc`, `units`, `subtotal`, `discount` |
| % IVA / % IRPF de cada línea | `taxes`: `s_iva_21`, `s_ret_15`... IVA 0 %: `s_iva_intras` (UE) o `s_iva_nosujeto` (fuera de la UE) |
| PDF (`download_pdf_url`) | Adjunto principal del documento |
| `accounting_account_code` (p. ej. 70500003) | `accountingAccountId` de cada línea: la misma subcuenta. Si no existe en Holded, se crea con el nombre de la (sub)categoría contable de Quipu |

Holded crea las subcuentas en «la siguiente libre» de un prefijo de 4 dígitos, así que solo se
crean automáticamente si el número resultante es seguro; si no, la carga se para y pide crearla a
mano. Si una factura ya migrada sigue en **borrador** en Holded, volver a cargarla la **actualiza**.
Las ya validadas no se tocan, para no generar registros nuevos en Verifactu.

Se usa `applyContactDefaults: false` para que Holded respete los impuestos de Quipu. Antes de
enviar se comprueba que las líneas cuadran con `total_amount`; después de crear la factura se
verifica el total en Holded. **No se importan** los borradores de Quipu (no están emitidos) ni,
por ahora, las facturas rectificativas.

Los PDF descargados se guardan en `/data/files/` (en el volumen, junto a la BD). Si la factura se
crea en Holded pero falla un paso posterior (adjuntar o verificar), se guarda igualmente su id:
el registro queda en error con el motivo y nunca se duplica.

## Puesta en marcha con Docker

```bash
cp .env.example .env        # y rellena las credenciales de Quipu y Holded
docker compose up -d --build
```

- Aplicación (frontend): <http://localhost:8080>
- API y Swagger (backend): <http://localhost:8000/docs>

Los puertos se cambian con `FRONTEND_PORT` y `BACKEND_PORT` en `.env`.

> Si modificas `.env` con los contenedores en marcha, aplica los cambios con `docker compose up -d`:
> recrea el backend con los valores nuevos. `docker compose restart` **no** vuelve a leer el `.env`.

Al arrancar, el backend ejecuta `alembic upgrade head` sobre la base de datos del volumen `qth-data`
(`/data/quipu_to_holded.db`). El frontend espera a que el backend responda al healthcheck.

Copia de seguridad de la base de datos:

```bash
docker compose stop backend
docker compose cp backend:/data/quipu_to_holded.db ./backup.db
docker compose start backend
```

## Desarrollo local (sin Docker)

Backend (puerto 8000). Sin Docker, la BD se crea en `backend/data/`:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows (en Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Frontend (puerto 5173; Vite redirige `/api` al backend):

```bash
cd frontend
npm install
npm run dev
```

Tests y lint del backend. También se pueden ejecutar dentro de `python:3.12-slim`:

```bash
docker run --rm -v "$PWD/backend:/src" -w /src python:3.12-slim sh -c "pip install -q -r requirements-dev.txt && pytest -q && ruff check ."
```

## Migraciones de base de datos (Alembic)

```bash
cd backend
alembic revision --autogenerate -m "descripcion del cambio"
alembic upgrade head
```

- La URL de la BD sale de `app.config` (variable `DATABASE_URL`), no de `alembic.ini`.
- Está activado `render_as_batch`: SQLite apenas soporta `ALTER TABLE` y Alembic recrea la tabla.
- El test `test_models_match_migrations` falla si cambias un modelo y no generas la migración.

## Estructura

```
├── docker-compose.yml       # servicios backend + frontend y volumen qth-data
├── backend/
│   ├── Dockerfile           # imagen python:3.12-slim
│   ├── alembic.ini
│   ├── entrypoint.sh        # alembic upgrade head + uvicorn
│   ├── migrations/          # entorno y versiones de Alembic
│   ├── app/
│   │   ├── main.py          # aplicación FastAPI (todo bajo /api)
│   │   ├── config.py        # configuración (.env / variables de entorno)
│   │   ├── db.py            # engine SQLite (WAL, foreign keys) y sesiones
│   │   ├── models.py        # MigrationRun, Record
│   │   ├── schemas.py       # modelos Pydantic del API
│   │   ├── api/             # endpoints: connections, entities, runs
│   │   ├── clients/         # clientes HTTP de Quipu y Holded
│   │   └── importer/
│   │       ├── base.py      # interfaz EntityHandler (extract / transform / load)
│   │       ├── registry.py  # entidades disponibles, ordenadas por dependencias
│   │       ├── pipeline.py  # fases en segundo plano
│   │       └── entities/    # un handler por tipo de entidad
│   └── tests/
└── frontend/
    ├── Dockerfile           # compila con Node y sirve con nginx
    ├── nginx.conf           # estáticos + proxy de /api al backend
    └── src/
        ├── App.tsx          # asistente y navegación entre pasos
        ├── steps/           # un componente por paso
        ├── components/      # stepper, tablas, badges
        └── api.ts           # cliente tipado del API
```

## Añadir una entidad

1. Crea un `EntityHandler` en `backend/app/importer/entities/` con `extract`, `transform` y `load`.
2. Regístralo en `backend/app/importer/registry.py`, después de las entidades de las que depende.

El frontend la muestra automáticamente (lee `/api/entities`).

## Estado del esqueleto / pendiente

- [x] **Contactos**: tipo (cliente/acreedor), operación fiscal y VAT intracomunitario validado en VIES.
- [ ] Contactos: persona física (`isperson`) y cuentas contables (`clientRecord`/`supplierRecord`).
- [x] **Facturas emitidas**: borrador en Holded, líneas e impuestos, PDF adjunto y Verifactu.
- [ ] Facturas emitidas: rectificativas y cobros (`paid_at`).
- [ ] **Gastos**: `transform` está sin implementar. En la Revisión aparecen como error a propósito.
- [x] API de Quipu contrastado con datos reales: paginación (`page[number]`), `include=items` y
      límite de peticiones (reintentos ante 429).
- [ ] Más entidades: productos, cobros/pagos, series de numeración, impuestos...
- [ ] Límites de peticiones del API de Holded (reintentos con espera).
