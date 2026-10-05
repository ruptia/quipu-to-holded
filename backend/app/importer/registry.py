from collections.abc import Iterable

from app.importer.base import EntityHandler
from app.importer.entities.contacts import ContactsHandler
from app.importer.entities.documents import ExpensesHandler, InvoicesHandler

# El orden importa: las dependencias van antes (se extrae y carga en este orden)
HANDLERS: dict[str, EntityHandler] = {
    handler.entity_type: handler
    for handler in (ContactsHandler(), InvoicesHandler(), ExpensesHandler())
}


def ordered(entity_types: Iterable[str]) -> list[str]:
    wanted = set(entity_types)
    return [entity_type for entity_type in HANDLERS if entity_type in wanted]
