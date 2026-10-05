from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import RecordStatus, RunStatus


class ConnectionStatus(BaseModel):
    configured: bool
    ok: bool
    detail: str | None = None


class ConnectionsOut(BaseModel):
    quipu: ConnectionStatus
    holded: ConnectionStatus


class EntityOut(BaseModel):
    type: str
    label: str
    depends_on: list[str]
    # Decisiones que el usuario puede tomar por registro (p. ej. "supplied_lines")
    overridable: list[str] = []
    # El documento original llega por fuera del API de Quipu
    external_documents: bool = False


class RunCreate(BaseModel):
    entities: list[str] = Field(min_length=1)


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: RunStatus
    entities: list[str]
    error: str | None
    created_at: datetime
    updated_at: datetime
    # entity_type -> estado del registro -> número de registros
    counts: dict[str, dict[str, int]] = {}


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    source_id: str
    status: RecordStatus
    target_id: str | None
    summary: str | None
    error: str | None
    overrides: dict[str, Any] | None = None


class RecordDetail(RecordOut):
    source_payload: dict[str, Any]
    target_payload: dict[str, Any] | None


class RecordPage(BaseModel):
    total: int
    items: list[RecordOut]


class PhaseRequest(BaseModel):
    """Registros sobre los que actuar. Vacío o ausente: todos los que correspondan."""

    record_ids: list[int] | None = None


class RecordOverrides(BaseModel):
    # Índices (en el orden de Quipu) de las líneas que son suplidos
    supplied_lines: list[int] = Field(default_factory=list)


class DocumentSource(BaseModel):
    url: str


class ExpenseBrief(BaseModel):
    record_id: int
    entity_type: str
    source_id: str
    date: str | None
    number: str | None
    issuer: str | None
    total: str | None


class DocumentMatchOut(ExpenseBrief):
    file: str  # nombre original del documento
    method: str  # "número" (ancla) u "orden"
    check: str  # "importe", "número", "emisor", "sin verificar" o "no cuadra"


class QuipuExportReport(BaseModel):
    matched: list[DocumentMatchOut]
    unmatched_files: list[str]
    expenses_without_file: list[ExpenseBrief]
    amortizations: list[ExpenseBrief]
    ignored_files: list[str]  # no siguen el formato del exportador o no son PDF/PNG/JPEG
    reset_to_extracted: int  # transformados que hay que volver a transformar
