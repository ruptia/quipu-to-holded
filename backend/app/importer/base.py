from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from app.clients import HoldedClient, QuipuClient

type IdResolver = Callable[[str, str], str | None]
"""(entity_type, id en Quipu) -> id en Holded si ya está migrado, o None."""


class RecordError(ValueError):
    """Fallo esperado en un registro concreto: se marca el registro y la fase continúa."""


@dataclass(frozen=True)
class Transformed:
    payload: dict[str, Any]
    # Resumen legible que se muestra en la revisión (p. ej. «Acreedor · Intracomunitario»)
    summary: str | None = None


class EntityHandler(ABC):
    """Cómo se migra un tipo de entidad. Añade uno nuevo y regístralo en registry.py."""

    entity_type: ClassVar[str]
    label: ClassVar[str]
    # Entidades que deben estar en Holded antes (p. ej. facturas -> contactos)
    depends_on: ClassVar[tuple[str, ...]] = ()
    # Si es True, lo migrado en ejecuciones anteriores se actualiza con update();
    # si no, se marca como «Ya migrado» y no se toca
    updatable: ClassVar[bool] = False

    @abstractmethod
    def extract(self, quipu: QuipuClient) -> Iterator[tuple[str, dict[str, Any]]]:
        """Devuelve (id en Quipu, payload original) por cada registro."""

    @abstractmethod
    def transform(self, source: dict[str, Any]) -> Transformed:
        """Convierte el payload de Quipu al de Holded. Nunca escribe en Holded."""

    @abstractmethod
    def load(self, holded: HoldedClient, payload: dict[str, Any], resolve: IdResolver) -> str:
        """Crea el registro en Holded y devuelve su id."""

    def update(
        self, holded: HoldedClient, target_id: str, payload: dict[str, Any], resolve: IdResolver
    ) -> None:
        """Actualiza en Holded un registro ya migrado (solo si `updatable`)."""
        raise NotImplementedError


def compact(value: Any) -> Any:
    """Elimina recursivamente None, cadenas vacías y diccionarios que queden vacíos."""
    if isinstance(value, dict):
        cleaned = {k: compact(v) for k, v in value.items()}
        return {k: v for k, v in cleaned.items() if v not in (None, "", {})}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value
