from alembic import command

from tests.conftest import alembic_config


def test_models_match_migrations():
    # Falla si los modelos tienen cambios sin migración (equivale a `alembic check`)
    command.check(alembic_config())
