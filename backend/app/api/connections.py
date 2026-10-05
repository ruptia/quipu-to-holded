from collections.abc import Callable

from fastapi import APIRouter

from app.api.deps import SettingsDep
from app.clients import HoldedClient, QuipuClient
from app.schemas import ConnectionsOut, ConnectionStatus

router = APIRouter(tags=["connections"])


def _check(configured: bool, ping: Callable[[], None]) -> ConnectionStatus:
    if not configured:
        return ConnectionStatus(configured=False, ok=False, detail="Faltan credenciales en .env")
    try:
        ping()
    except Exception as exc:
        return ConnectionStatus(configured=True, ok=False, detail=str(exc))
    return ConnectionStatus(configured=True, ok=True)


@router.get("/connections", response_model=ConnectionsOut)
def check_connections(settings: SettingsDep) -> ConnectionsOut:
    def ping_quipu() -> None:
        with QuipuClient.from_settings(settings) as client:
            client.ping()

    def ping_holded() -> None:
        with HoldedClient.from_settings(settings) as client:
            client.ping()

    return ConnectionsOut(
        quipu=_check(settings.quipu_configured, ping_quipu),
        holded=_check(settings.holded_configured, ping_holded),
    )
