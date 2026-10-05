import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.api import api_router
from app.importer.pipeline import recover_interrupted_runs

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        recover_interrupted_runs()
    except OperationalError:
        log.warning("No se puede acceder a la BD. ¿Has ejecutado 'alembic upgrade head'?")
    yield


app = FastAPI(title="Quipu → Holded", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
