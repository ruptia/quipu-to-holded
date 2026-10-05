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
mano. Holded tampoco permite crear por API cuentas base (xxxx0000): si Quipu usa una que no existe
en Holded (p. ej. 63100000 Otros tributos), se usa la primera subcuenta del grupo (63100001), que se
crea si hace falta. Si una factura ya migrada sigue en **borrador** en Holded, volver a cargarla la **actualiza**.
Las ya validadas no se tocan, para no generar registros nuevos en Verifactu.

Se usa `applyContactDefaults: false` para que Holded respete los impuestos de Quipu. Antes de
enviar se comprueba que las líneas cuadran con `total_amount`; después de crear la factura se
verifica el total en Holded. **No se importan** los borradores de Quipu (no están emitidos) ni,
por ahora, las facturas rectificativas.

### Gastos y tickets

Las facturas de gasto (`/invoices?filter[kind]=expenses`) y los tickets (`/simplified_invoices`)
se crean en Holded como **compras en borrador**. Cada línea de Quipu se reparte según su
deducibilidad:

| Quipu (por línea) | Holded |
|---|---|
| % IVA deducible | Parte deducible con `p_iva_X`, el resto con `p_iva_nd_X` (IVA no deducible) |
| % gasto deducible (IRPF) | Parte deducible en su cuenta de gasto; el resto en la subcuenta «<cuenta> – no deducible IRPF» (se crea si falta) |
| IVA 0 % | `p_iva_0`, igual que en Quipu |
| `kind: asset` (bien de inversión) | Cuenta de inmovilizado de la línea (p. ej. 21700000) con `p_iva_bi_X` |
| Línea marcada como suplido en la revisión | `supplied: "Yes"`, sin reparto |
| % de IVA calculado (10,02 %, 20,9 %) | Se redondea al tipo de Holded y se avisa en la revisión |
| Ticket sin contacto | `contactCode` (NIF del emisor) y `contactName`: Holded lo asocia o lo crea |

Las **cuotas de amortización** (cuenta 68x) **no se importan**: Quipu las registra como gastos,
pero en Holded las genera el módulo de activos al dar de alta el bien. Importarlas las duplicaría.

**Documento original:** el API de Quipu no permite leer el adjunto de los gastos (solo subirlo, y
`download_pdf_url` solo existe en las facturas emitidas). Se usa el **exportador de Quipu**: en la
Revisión, «Documentos de los gastos» → selecciona la carpeta descargada (o su ZIP). El exportador
nombra cada fichero `<cuenta>-<N>-1-…-<nombre original>`, donde `N` sigue el orden de creación en
Quipu (el id del gasto). El emparejamiento:

1. **Anclas:** ficheros cuyo nombre original contiene el número de un único gasto.
2. **Orden:** entre dos anclas, ficheros y gastos van en el mismo orden; si hay tantos de unos como
   de otros, se emparejan uno a uno (si no, quedan en el informe para revisarlos).
3. Las cuotas de amortización (68x) no tienen documento y no cuentan.
4. **Verificación:** en cada PDF se busca el total del gasto, su número o el emisor. Las imágenes
   quedan «sin verificar».

Los documentos se guardan en `/data/files/<expenses|tickets>/<id>.<ext>` y se adjuntan al cargar. La
revisión avisa con «⚠ sin documento» si a un gasto le falta. También se puede registrar uno suelto
por URL con `POST /api/files/{expenses|tickets}/{id}` (`{url}`).

### Selección de registros

En la **Revisión** eliges qué registros transformar (también los ya cargados, para corregirlos) y,
en los gastos, marcas los suplidos. En la **Carga** solo se envían los registros seleccionados.
Las marcas de suplido se guardan por registro de Quipu y se heredan en ejecuciones nuevas.

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
