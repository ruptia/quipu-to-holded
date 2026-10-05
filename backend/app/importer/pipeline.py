"""Fases de una ejecución: extracción (Quipu) → transformación → carga (Holded).

Cada fase corre en segundo plano con su propia sesión y deja la ejecución en
`done_status` o en FAILED. Los errores de un registro concreto no paran la fase.
"""

import logging
from collections import defaultdict
from collections.abc import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.clients import HoldedClient, QuipuClient
from app.config import get_settings
from app.db import SessionLocal
from app.importer.base import IdResolver, PartialLoadError, RecordError
from app.importer.registry import HANDLERS
from app.models import BUSY_STATUSES, MigrationRun, Record, RecordStatus, RunStatus

log = logging.getLogger(__name__)

BATCH_SIZE = 200
TRANSFORMABLE = (RecordStatus.EXTRACTED, RecordStatus.TRANSFORMED, RecordStatus.ERROR)
FINAL = (RecordStatus.LOADED, RecordStatus.UPDATED, RecordStatus.SKIPPED)


def _run_phase(
    run_id: int, done_status: RunStatus, body: Callable[[Session, MigrationRun], None]
) -> None:
    with SessionLocal() as session:
        run = session.get_one(MigrationRun, run_id)
        try:
            body(session, run)
            run.status = done_status
        except Exception as exc:
            log.exception("La ejecución %s ha fallado", run_id)
            session.rollback()
            run.status = RunStatus.FAILED
            run.error = str(exc) or type(exc).__name__
        session.commit()


def _mark_error(record: Record, exc: Exception) -> None:
    if not isinstance(exc, RecordError):
        log.exception("Error inesperado en %s %s", record.entity_type, record.source_id)
    record.status = RecordStatus.ERROR
    record.error = str(exc) or type(exc).__name__


def extract_run(run_id: int) -> None:
    def body(session: Session, run: MigrationRun) -> None:
        with QuipuClient.from_settings(get_settings()) as quipu:
            for entity_type in run.entities:
                existing = {
                    r.source_id: r
                    for r in session.scalars(
                        select(Record).where(
                            Record.run_id == run.id, Record.entity_type == entity_type
                        )
                    )
                }
                extracted = HANDLERS[entity_type].extract(quipu)
                for i, (source_id, payload) in enumerate(extracted, start=1):
                    record = existing.get(source_id)
                    if record is None:
                        record = Record(
                            run_id=run.id,
                            entity_type=entity_type,
                            source_id=source_id,
                            source_payload=payload,
                            overrides=_previous_overrides(session, entity_type, source_id),
                        )
                        session.add(record)
                        existing[source_id] = record
                    elif record.status not in FINAL:
                        record.source_payload = payload
                        record.target_payload = None
                        record.summary = None
                        record.status = RecordStatus.EXTRACTED
                        record.error = None
                    if i % BATCH_SIZE == 0:
                        session.commit()
                session.commit()

    _run_phase(run_id, RunStatus.EXTRACTED, body)


def _previous_overrides(session: Session, entity_type: str, source_id: str) -> dict | None:
    """Las decisiones del usuario (p. ej. suplidos) pertenecen al registro de Quipu, no a
    la ejecución: una ejecución nueva hereda las de la última que las tenga."""
    return session.scalar(
        select(Record.overrides)
        .where(
            Record.entity_type == entity_type,
            Record.source_id == source_id,
            Record.overrides.is_not(None),
        )
        .order_by(Record.id.desc())
        .limit(1)
    )


def transform_run(run_id: int, record_ids: list[int] | None = None) -> None:
    """Sin `record_ids`, transforma los pendientes. Con ellos, exactamente esos registros
    (aunque ya estén en Holded: así se pueden corregir y volver a enviar)."""

    def body(session: Session, run: MigrationRun) -> None:
        query = select(Record).where(Record.run_id == run.id).order_by(Record.id)
        if record_ids:
            query = query.where(Record.id.in_(record_ids))
        else:
            query = query.where(Record.status.in_(TRANSFORMABLE))
        records = session.scalars(query).all()
        for i, record in enumerate(records, start=1):
            try:
                result = HANDLERS[record.entity_type].transform(
                    record.source_payload, record.overrides
                )
                record.target_payload = result.payload
                record.summary = result.summary
                record.status = RecordStatus.TRANSFORMED
                record.error = None
            except Exception as exc:
                record.summary = None
                _mark_error(record, exc)
            if i % BATCH_SIZE == 0:
                session.commit()

    _run_phase(run_id, RunStatus.TRANSFORMED, body)


def _make_resolver(session: Session) -> IdResolver:
    def resolve(entity_type: str, source_id: str) -> str | None:
        return session.scalar(
            select(Record.target_id)
            .where(
                Record.entity_type == entity_type,
                Record.source_id == source_id,
                Record.target_id.is_not(None),
            )
            .limit(1)
        )

    return resolve


def load_run(run_id: int, record_ids: list[int] | None = None) -> None:
    """Envía a Holded los registros transformados (solo `record_ids` si se indican)."""

    def body(session: Session, run: MigrationRun) -> None:
        resolve = _make_resolver(session)
        with HoldedClient.from_settings(get_settings()) as holded:
            for entity_type in run.entities:
                handler = HANDLERS[entity_type]
                query = (
                    select(Record)
                    .where(
                        Record.run_id == run.id,
                        Record.entity_type == entity_type,
                        Record.status == RecordStatus.TRANSFORMED,
                    )
                    .order_by(Record.id)
                )
                if record_ids:
                    query = query.where(Record.id.in_(record_ids))
                records = session.scalars(query).all()
                if records:
                    handler.before_load(holded, [r.target_payload or {} for r in records])
                for record in records:
                    payload = record.target_payload or {}
                    # Id en Holded si se migró en una ejecución anterior
                    target_id = resolve(entity_type, record.source_id)
                    try:
                        if target_id and handler.updatable:
                            handler.update(holded, target_id, payload, resolve)
                            record.status = RecordStatus.UPDATED
                        elif target_id:
                            record.status = RecordStatus.SKIPPED
                        else:
                            target_id = handler.load(holded, payload, resolve)
                            record.status = RecordStatus.LOADED
                        record.target_id = target_id
                        record.error = None
                    except PartialLoadError as exc:
                        record.target_id = exc.target_id  # ya existe en Holded: no duplicar
                        _mark_error(record, exc)
                    except Exception as exc:
                        _mark_error(record, exc)
                    # Commit por registro: si algo se crea en Holded tiene que quedar
                    # registrado aquí, o una reejecución lo duplicaría.
                    session.commit()

    _run_phase(run_id, RunStatus.COMPLETED, body)


def record_counts(session: Session, run_id: int) -> dict[str, dict[str, int]]:
    rows = session.execute(
        select(Record.entity_type, Record.status, func.count())
        .where(Record.run_id == run_id)
        .group_by(Record.entity_type, Record.status)
    )
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for entity_type, status, total in rows:
        counts[entity_type][status.value] = total
    return dict(counts)


def recover_interrupted_runs() -> None:
    """Al arrancar, ninguna fase puede estar en curso: el proceso anterior murió."""
    with SessionLocal() as session:
        session.execute(
            update(MigrationRun)
            .where(MigrationRun.status.in_(BUSY_STATUSES))
            .values(status=RunStatus.FAILED, error="Interrumpida por un reinicio del servidor")
        )
        session.commit()
