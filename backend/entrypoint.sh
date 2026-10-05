#!/bin/sh
set -e

# Aplica las migraciones pendientes sobre la base de datos del volumen
alembic upgrade head

# Un único worker: las fases de migración corren como tareas en segundo plano del proceso
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
