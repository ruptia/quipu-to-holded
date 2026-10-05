"""Ficheros de documentos guardados en el volumen: <files_dir>/<entity_type>/<source_id>.<ext>."""

from pathlib import Path

import httpx

EXTENSIONS = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}
CONTENT_TYPES = {extension: mime for mime, extension in EXTENSIONS.items()}
# Firmas de fichero, por si el servidor responde con un MIME genérico
MAGIC = {b"%PDF": "application/pdf", b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg"}


class FileFetchError(RuntimeError):
    pass


def sniff_content_type(content: bytes, declared: str) -> str | None:
    declared = declared.split(";")[0].strip().lower()
    if declared in EXTENSIONS:
        return declared
    for magic, mime in MAGIC.items():
        if content.startswith(magic):
            return mime
    return None


def save_document(
    files_dir: Path, entity_type: str, source_id: str, content: bytes, content_type: str
) -> str:
    """Guarda el documento (sustituye al anterior) y devuelve su ruta relativa."""
    folder = files_dir / entity_type
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{source_id}.*"):
        old.unlink()
    relative = Path(entity_type) / f"{source_id}.{EXTENSIONS[content_type]}"
    (files_dir / relative).write_bytes(content)
    return relative.as_posix()


def stored_document(files_dir: Path, entity_type: str, source_id: str) -> tuple[str, str] | None:
    """(ruta relativa, MIME) del documento guardado, o None."""
    for path in sorted((files_dir / entity_type).glob(f"{source_id}.*")):
        content_type = CONTENT_TYPES.get(path.suffix.lstrip(".").lower())
        if content_type:
            return path.relative_to(files_dir).as_posix(), content_type
    return None


def fetch_document(
    files_dir: Path, entity_type: str, source_id: str, url: str, timeout: float
) -> tuple[str, str, int]:
    """Descarga un documento por URL y lo guarda. Devuelve (ruta relativa, MIME, tamaño)."""
    try:
        response = httpx.get(url, follow_redirects=True, timeout=timeout)
    except httpx.HTTPError as exc:
        raise FileFetchError(f"No se pudo descargar el documento: {exc}") from exc
    if response.is_error:
        raise FileFetchError(f"La descarga del documento ha fallado ({response.status_code})")
    content_type = sniff_content_type(response.content, response.headers.get("content-type", ""))
    if content_type is None:
        raise FileFetchError("El fichero descargado no es un PDF, PNG ni JPEG")
    path = save_document(files_dir, entity_type, source_id, response.content, content_type)
    return path, content_type, len(response.content)
