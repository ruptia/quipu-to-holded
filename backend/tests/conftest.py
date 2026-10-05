import os
import shutil
import tempfile
from pathlib import Path

# La BD de pruebas debe fijarse antes de importar app.db (crea el engine al importarse)
_TMP_DIR = Path(tempfile.mkdtemp(prefix="qth-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import MigrationRun, Record  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    command.upgrade(alembic_config(), "head")
    yield
    engine.dispose()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture
def session():
    with SessionLocal() as s:
        yield s
    with engine.begin() as conn:
        conn.execute(delete(Record))
        conn.execute(delete(MigrationRun))


@pytest.fixture
def client(session):
    # Sin credenciales, para no depender del .env local
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, quipu_app_id="", quipu_app_secret="", holded_api_key=""
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
