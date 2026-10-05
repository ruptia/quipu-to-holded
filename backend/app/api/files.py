"""Documentos originales de los registros (p. ej. el PDF o la foto de un gasto).

El API de Quipu no permite descargar el documento de los gastos, así que su URL se obtiene
por otra vía (la web de Quipu) y aquí el backend lo descarga y lo guarda en el volumen.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import SettingsDep
from app.importer.files import FileFetchError, fetch_document, stored_document
from app.importer.registry import HANDLERS
from app.schemas import DocumentSource

router = APIRouter(prefix="/files", tags=["files"])


class StoredDocument(BaseModel):
    path: str
    content_type: str
    size: int


def _check_entity(entity_type: str) -> None:
    if entity_type not in HANDLERS or not HANDLERS[entity_type].external_documents:
        raise HTTPException(422, f"'{entity_type}' no admite documentos externos")


@router.post("/{entity_type}/{source_id}", response_model=StoredDocument, status_code=201)
def store_document(
    entity_type: str, source_id: str, body: DocumentSource, settings: SettingsDep
) -> StoredDocument:
    _check_entity(entity_type)
    if not body.url.startswith("https://"):
        raise HTTPException(422, "La URL del documento debe ser https")
    try:
        path, content_type, size = fetch_document(
            settings.resolved_files_dir, entity_type, source_id, body.url, settings.http_timeout
        )
    except FileFetchError as exc:
        raise HTTPException(502, str(exc)) from exc
    return StoredDocument(path=path, content_type=content_type, size=size)


@router.get("/{entity_type}/{source_id}", response_model=StoredDocument)
def get_document(entity_type: str, source_id: str, settings: SettingsDep) -> StoredDocument:
    _check_entity(entity_type)
    found = stored_document(settings.resolved_files_dir, entity_type, source_id)
    if found is None:
        raise HTTPException(404, "No hay documento guardado para este registro")
    path, content_type = found
    size = (settings.resolved_files_dir / path).stat().st_size
    return StoredDocument(path=path, content_type=content_type, size=size)
