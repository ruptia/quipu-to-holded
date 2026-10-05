from fastapi import APIRouter

from app.importer.registry import HANDLERS
from app.schemas import EntityOut

router = APIRouter(tags=["entities"])


@router.get("/entities", response_model=list[EntityOut])
def list_entities() -> list[EntityOut]:
    return [
        EntityOut(type=h.entity_type, label=h.label, depends_on=list(h.depends_on))
        for h in HANDLERS.values()
    ]
