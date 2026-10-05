"""Importación de los documentos de los gastos desde el exportador de Quipu."""

import io
import mimetypes
import zipfile
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy import select

from app.api.deps import SessionDep, SettingsDep
from app.importer.files import save_document, sniff_content_type
from app.importer.quipu_export import (
    ExpenseRef,
    match_documents,
    parse_export_name,
    verify_document,
)
from app.importer.registry import HANDLERS
from app.models import BUSY_STATUSES, MigrationRun, Record, RecordStatus
from app.schemas import DocumentMatchOut, ExpenseBrief, QuipuExportReport

router = APIRouter(prefix="/runs", tags=["documents"])


def _uploaded_files(uploads: list[UploadFile]) -> dict[str, bytes]:
    """Nombre → contenido. Los ZIP se abren (el exportador de Quipu descarga un ZIP)."""
    files: dict[str, bytes] = {}
    for upload in uploads:
        content = upload.file.read()
        name = PurePath(upload.filename or "").name  # al subir una carpeta llega con su ruta
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    for entry in archive.infolist():
                        if not entry.is_dir():
                            files[PurePath(entry.filename).name] = archive.read(entry)
            except zipfile.BadZipFile as exc:
                raise HTTPException(422, f"'{name}' no es un ZIP válido") from exc
        elif name:
            files[name] = content
    return files


def _brief(record: Record) -> ExpenseBrief:
    attrs = record.source_payload.get("attributes") or {}
    return ExpenseBrief(
        record_id=record.id,
        entity_type=record.entity_type,
        source_id=record.source_id,
        date=attrs.get("issue_date"),
        number=attrs.get("number"),
        issuer=attrs.get("issuing_name"),
        total=attrs.get("total_amount"),
    )


@router.post("/{run_id}/documents/quipu-export", response_model=QuipuExportReport)
def import_quipu_export(
    run_id: int,
    session: SessionDep,
    settings: SettingsDep,
    files: Annotated[list[UploadFile], File(description="Ficheros del exportador o su ZIP")],
) -> QuipuExportReport:
    run = session.get(MigrationRun, run_id)
    if run is None:
        raise HTTPException(404, "Ejecución no encontrada")
    if run.status in BUSY_STATUSES:
        raise HTTPException(409, "La ejecución tiene una fase en curso")
    entity_types = [t for t in run.entities if HANDLERS[t].external_documents]
    records = {
        r.id: r
        for r in session.scalars(
            select(Record).where(Record.run_id == run_id, Record.entity_type.in_(entity_types))
        )
    }
    if not records:
        raise HTTPException(422, "Esta ejecución no tiene gastos ni tickets extraídos")

    uploaded = _uploaded_files(files)
    parsed = {name: parse_export_name(name) for name in uploaded}
    ignored = sorted(name for name, export_file in parsed.items() if export_file is None)
    expenses = [
        ExpenseRef(
            key=record.id,
            quipu_id=int(record.source_id),
            number=(record.source_payload.get("attributes") or {}).get("number"),
            account=(record.source_payload.get("attributes") or {}).get("accounting_account_code"),
        )
        for record in records.values()
    ]
    result = match_documents([f for f in parsed.values() if f], expenses)

    matched, reset = [], 0
    for match in result.matches:
        record = records[match.expense.key]
        content = uploaded[match.file.name]
        guessed = mimetypes.guess_type(match.file.original)[0] or ""
        content_type = sniff_content_type(content, guessed)
        if content_type is None:
            ignored.append(match.file.original)
            continue
        save_document(
            settings.resolved_files_dir, record.entity_type, record.source_id, content, content_type
        )
        brief = _brief(record)
        check = verify_document(
            content, content_type, total=brief.total, number=brief.number, issuer=brief.issuer
        )
        matched.append(
            DocumentMatchOut(
                **brief.model_dump(), file=match.file.original, method=match.method, check=check
            )
        )
        # Lo ya transformado no lleva el documento: hay que volver a transformarlo
        if record.status == RecordStatus.TRANSFORMED:
            record.status = RecordStatus.EXTRACTED
            record.target_payload = None
            record.summary = None
            reset += 1
    session.commit()

    return QuipuExportReport(
        matched=matched,
        unmatched_files=[f.original for f in result.unmatched_files],
        expenses_without_file=[_brief(records[e.key]) for e in result.unmatched_expenses],
        amortizations=[_brief(records[e.key]) for e in result.without_document],
        ignored_files=ignored,
        reset_to_extracted=reset,
    )
