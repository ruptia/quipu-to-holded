from fastapi import APIRouter

from app.api import connections, entities, runs

api_router = APIRouter(prefix="/api")
api_router.include_router(connections.router)
api_router.include_router(entities.router)
api_router.include_router(runs.router)


@api_router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}
