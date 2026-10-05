from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Nombres de constraints deterministas: Alembic los necesita en SQLite (modo batch)
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite no guarda zona horaria: se almacena en UTC y se devuelve con tzinfo=UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


def _str_enum(enum_cls: type[StrEnum]) -> Enum:
    """Guarda el valor del enum (no el nombre) en un VARCHAR."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=20,
        values_callable=lambda members: [m.value for m in members],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class RunStatus(StrEnum):
    CREATED = "created"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    TRANSFORMING = "transforming"
    TRANSFORMED = "transformed"
    LOADING = "loading"
    COMPLETED = "completed"
    FAILED = "failed"


BUSY_STATUSES = frozenset({RunStatus.EXTRACTING, RunStatus.TRANSFORMING, RunStatus.LOADING})


class RecordStatus(StrEnum):
    EXTRACTED = "extracted"
    TRANSFORMED = "transformed"
    LOADED = "loaded"
    UPDATED = "updated"  # ya existía en Holded y se ha actualizado
    SKIPPED = "skipped"  # ya existía en Holded y la entidad no admite actualizar
    ERROR = "error"


class MigrationRun(Base):
    """Una ejecución del asistente: qué entidades se migran y en qué fase está."""

    __tablename__ = "migration_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[RunStatus] = mapped_column(_str_enum(RunStatus), default=RunStatus.CREATED)
    entities: Mapped[list[str]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    records: Mapped[list["Record"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class Record(Base):
    """Un registro de Quipu en tránsito: payload original, payload para Holded e id final."""

    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("run_id", "entity_type", "source_id"),
        # Búsqueda entre ejecuciones: ¿este id de Quipu ya está migrado a Holded?
        Index("ix_records_entity_source", "entity_type", "source_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("migration_runs.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[RecordStatus] = mapped_column(
        _str_enum(RecordStatus), default=RecordStatus.EXTRACTED
    )
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    target_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    target_id: Mapped[str | None] = mapped_column(String(100))
    # Resumen legible de la transformación (p. ej. «Acreedor · Intracomunitario (...)»)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    run: Mapped[MigrationRun] = relationship(back_populates="records")
