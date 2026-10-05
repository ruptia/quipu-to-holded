from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, defer

from app.api.deps import SessionDep, SettingsDep
from app.importer import pipeline
from app.importer.registry import HANDLERS, ordered
from app.models import BUSY_STATUSES, MigrationRun, Record, RecordStatus, RunStatus
from app.schemas import (
    PhaseRequest,
    RecordDetail,
    RecordOverrides,
    RecordPage,
    RunCreate,
    RunOut,
)

router = APIRouter(prefix="/runs", tags=["runs"])


def _get_run(session: Session, run_id: int) -> MigrationRun:
    run = session.get(MigrationRun, run_id)
    if run is None:
        raise HTTPException(404, "Ejecución no encontrada")
    return run


def _to_out(session: Session, run: MigrationRun) -> RunOut:
    out = RunOut.model_validate(run)
    out.counts = pipeline.record_counts(session, run.id)
    return out


def _start_phase(
    session: Session,
    background: BackgroundTasks,
    run_id: int,
    status: RunStatus,
    task: Callable[..., None],
    *task_args: Any,
) -> RunOut:
    run = _get_run(session, run_id)
    # UPDATE condicional: evita lanzar dos fases a la vez sobre la misma ejecución
    result = session.execute(
        update(MigrationRun)
        .where(MigrationRun.id == run_id, MigrationRun.status.not_in(BUSY_STATUSES))
        .values(status=status, error=None)
    )
    session.commit()
    if result.rowcount == 0:
        raise HTTPException(409, "La ejecución ya tiene una fase en curso")
    background.add_task(task, run_id, *task_args)
    session.refresh(run)
    return _to_out(session, run)


@router.post("", response_model=RunOut, status_code=201)
def create_run(body: RunCreate, session: SessionDep) -> RunOut:
    unknown = set(body.entities) - HANDLERS.keys()
    if unknown:
        raise HTTPException(422, f"Entidades desconocidas: {', '.join(sorted(unknown))}")
    run = MigrationRun(entities=ordered(body.entities))
    session.add(run)
    session.commit()
    return _to_out(session, run)


@router.get("", response_model=list[RunOut])
def list_runs(session: SessionDep, limit: int = Query(20, ge=1, le=100)) -> list[RunOut]:
    runs = session.scalars(select(MigrationRun).order_by(MigrationRun.id.desc()).limit(limit))
    return [_to_out(session, run) for run in runs]


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: int, session: SessionDep) -> RunOut:
    return _to_out(session, _get_run(session, run_id))


@router.post("/{run_id}/extract", response_model=RunOut, status_code=202)
def extract(
    run_id: int, background: BackgroundTasks, session: SessionDep, settings: SettingsDep
) -> RunOut:
    if not settings.quipu_configured:
        raise HTTPException(400, "Faltan las credenciales de Quipu en el fichero .env")
    return _start_phase(session, background, run_id, RunStatus.EXTRACTING, pipeline.extract_run)


@router.post("/{run_id}/transform", response_model=RunOut, status_code=202)
def transform(
    run_id: int,
    background: BackgroundTasks,
    session: SessionDep,
    body: PhaseRequest | None = None,
) -> RunOut:
    record_ids = body.record_ids if body else None
    return _start_phase(
        session, background, run_id, RunStatus.TRANSFORMING, pipeline.transform_run, record_ids
    )


@router.post("/{run_id}/load", response_model=RunOut, status_code=202)
def load(
    run_id: int,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    body: PhaseRequest | None = None,
) -> RunOut:
    if not settings.holded_configured:
        raise HTTPException(400, "Falta la API key de Holded en el fichero .env")
    record_ids = body.record_ids if body else None
    return _start_phase(
        session, background, run_id, RunStatus.LOADING, pipeline.load_run, record_ids
    )


@router.get("/{run_id}/records", response_model=RecordPage)
def list_records(
    run_id: int,
    session: SessionDep,
    entity_type: str | None = None,
    status: RecordStatus | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> RecordPage:
    _get_run(session, run_id)
    query = select(Record).where(Record.run_id == run_id)
    if entity_type:
        query = query.where(Record.entity_type == entity_type)
    if status:
        query = query.where(Record.status == status)

    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(
        query.options(defer(Record.source_payload), defer(Record.target_payload))
        .order_by(Record.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return RecordPage(total=total, items=items)


@router.get("/{run_id}/records/{record_id}", response_model=RecordDetail)
def get_record(run_id: int, record_id: int, session: SessionDep) -> Record:
    record = session.get(Record, record_id)
    if record is None or record.run_id != run_id:
        raise HTTPException(404, "Registro no encontrado")
    return record


@router.put("/{run_id}/records/{record_id}/overrides", response_model=RecordDetail)
def set_overrides(
    run_id: int, record_id: int, body: RecordOverrides, session: SessionDep
) -> Record:
    run = _get_run(session, run_id)
    if run.status in BUSY_STATUSES:
        raise HTTPException(409, "La ejecución tiene una fase en curso")
    record = session.get(Record, record_id)
    if record is None or record.run_id != run_id:
        raise HTTPException(404, "Registro no encontrado")
    if "supplied_lines" not in HANDLERS[record.entity_type].overridable:
        raise HTTPException(422, "Este tipo de registro no admite marcar suplidos")
    lines = len(record.source_payload.get("items") or [])
    if any(not 0 <= index < lines for index in body.supplied_lines):
        raise HTTPException(422, f"Índice de línea fuera de rango (el registro tiene {lines})")

    record.overrides = {"supplied_lines": sorted(set(body.supplied_lines))}
    # Hay que volver a transformarlo; target_id se conserva para actualizar, no duplicar
    record.status = RecordStatus.EXTRACTED
    record.target_payload = None
    record.summary = None
    record.error = None
    session.commit()
    return record
