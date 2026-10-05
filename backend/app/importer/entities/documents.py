from collections.abc import Iterator
from typing import Any, ClassVar

from app.clients import HoldedClient, QuipuClient
from app.importer.base import EntityHandler, IdResolver, RecordError, Transformed

# Clave auxiliar que deja transform() para resolver el contacto en la carga
QUIPU_CONTACT_KEY = "_quipuContactId"


class DocumentHandler(EntityHandler):
    """Base para facturas de Quipu que se convierten en documentos de Holded."""

    depends_on = ("contacts",)
    quipu_kind: ClassVar[str]
    holded_doc_type: ClassVar[str]

    def extract(self, quipu: QuipuClient) -> Iterator[tuple[str, dict[str, Any]]]:
        for item in quipu.paginate("/invoices", params={"filter[kind]": self.quipu_kind}):
            yield str(item["id"]), item

    def transform(self, source: dict[str, Any]) -> Transformed:
        # TODO: mapear cabecera (fecha, numeración, vencimiento) y líneas (importes, IVA, IRPF).
        # Debe devolver el payload de Holded + QUIPU_CONTACT_KEY con el id del contacto en Quipu:
        #   contact = source["relationships"]["contact"]["data"]["id"]
        raise RecordError(f"Transformación de '{self.label}' pendiente de implementar")

    def load(self, holded: HoldedClient, payload: dict[str, Any], resolve: IdResolver) -> str:
        payload = dict(payload)
        quipu_contact_id = payload.pop(QUIPU_CONTACT_KEY)
        contact_id = resolve("contacts", quipu_contact_id)
        if contact_id is None:
            raise RecordError(f"El contacto {quipu_contact_id} de Quipu no está migrado a Holded")
        payload["contactId"] = contact_id
        return holded.create_document(self.holded_doc_type, payload)


class InvoicesHandler(DocumentHandler):
    entity_type = "invoices"
    label = "Facturas emitidas"
    quipu_kind = "income"
    holded_doc_type = "invoice"


class ExpensesHandler(DocumentHandler):
    entity_type = "expenses"
    label = "Gastos (facturas recibidas)"
    quipu_kind = "expenses"
    holded_doc_type = "purchase"
