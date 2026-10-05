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


class RecordDetail(RecordOut):
    source_payload: dict[str, Any]
    target_payload: dict[str, Any] | None


class RecordPage(BaseModel):
    total: int
    items: list[RecordOut]
