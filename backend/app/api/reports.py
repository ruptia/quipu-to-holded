from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.models import Record
from app.reports.taxes import SourceDocument, tax_report

router = APIRouter(prefix="/reports", tags=["reports"])

DOCUMENT_TYPES = ("invoices", "expenses", "tickets")


@router.get("/taxes")
def taxes(
    session: SessionDep, year: int | None = Query(None), simplified: bool = Query(True)
) -> dict[str, Any]:
    """Cuadre orientativo del 303 y el 130 por trimestre con la última extracción de cada
    documento de Quipu (de cualquier ejecución)."""
    latest = (
        select(func.max(Record.id))
        .where(Record.entity_type.in_(DOCUMENT_TYPES))
        .group_by(Record.entity_type, Record.source_id)
    )
    records = session.scalars(select(Record).where(Record.id.in_(latest))).all()
    documents = [SourceDocument(r.entity_type, r.source_payload, r.overrides) for r in records]
    years = sorted(
        {
            int(day[:4])
            for d in documents
            if (day := (d.payload.get("attributes") or {}).get("issue_date"))
        }
    )
    return tax_report(documents, year or (years[-1] if years else 0), simplified)
