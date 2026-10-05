"""Facturas de Quipu → documentos de Holded.

Extracción: listado con include=items (las líneas llegan en `included`), nombre de la
categoría contable y descarga del fichero de cada factura (PDF) al volumen.

Verifactu: las facturas emitidas ya están registradas en la AEAT desde Quipu. En Holded se
crean SIEMPRE como borrador (approveDoc=false), porque aprobarlas desde el API las enviaría
otra vez a Verifactu. Antes de aprobarlas en Holded hay que marcar «No enviar a Verifactu»
en las opciones de la factura. Por lo mismo, solo se actualizan facturas que sigan en borrador.

Cuenta contable: cada línea va a la misma subcuenta que en Quipu (accounting_account_code).
Si no existe en Holded, se crea con el nombre de la categoría contable de Quipu.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, ClassVar

import httpx

from app.clients import HoldedClient, QuipuClient, QuipuError
from app.config import get_settings
from app.importer.base import (
    EntityHandler,
    IdResolver,
    PartialLoadError,
    RecordError,
    Transformed,
    compact,
)
from app.importer.entities.contacts import EU_VAT_PREFIXES, vat_prefix

# Claves auxiliares del payload transformado (no se envían a Holded)
QUIPU_CONTACT_KEY = "_quipuContactId"
ATTACHMENT_KEY = "_attachment"
EXPECTED_TOTAL_KEY = "_quipuTotal"
ACCOUNT_KEY = "_account"
# Claves que añade la extracción al payload de Quipu
ATTACHMENT_ERROR_KEY = "_attachmentError"
ACCOUNT_NAME_KEY = "_accountName"

EXTENSIONS = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}
CENT = Decimal("0.01")

SALES_VAT_KEYS = {
    Decimal(21): "s_iva_21",
    Decimal(10): "s_iva_10",
    Decimal("7.5"): "s_iva_75",
    Decimal(5): "s_iva_5",
    Decimal(4): "s_iva_4",
    Decimal(2): "s_iva_2",
}
SALES_RETENTION_KEYS = {Decimal(19): "s_ret_19", Decimal(15): "s_ret_15", Decimal(7): "s_ret_7"}

VERIFACTU_STAGES = {
    "approved_aeat": "Verifactu (Quipu): approved_aeat",
    "approved_with_errors_aeat": "⚠ Verifactu (Quipu): approved_with_errors_aeat",
}


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except InvalidOperation as exc:
        raise RecordError(f"Importe no numérico en Quipu: {value!r}") from exc


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _money(value: Decimal) -> str:
    """1234.5 → «1.234,50»."""
    text = f"{value.quantize(CENT):,.2f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


def _timestamp(day: str) -> int:
    """Fecha de Quipu (AAAA-MM-DD) → timestamp Unix a las 12:00 UTC.

    El mediodía UTC cae en el mismo día en cualquier huso horario europeo.
    """
    return int(datetime.combine(date.fromisoformat(day), time(12), tzinfo=UTC).timestamp())


@dataclass(frozen=True)
class Line:
    item: dict[str, Any]  # línea en formato Holded
    total: Decimal  # base + IVA - retención, para cuadrar con Quipu


class DocumentHandler(EntityHandler):
    """Base para facturas de Quipu que se convierten en documentos de Holded."""

    depends_on = ("contacts",)
    quipu_kind: ClassVar[str]
    holded_doc_type: ClassVar[str]

    # --- Extracción ---

    def extract(self, quipu: QuipuClient) -> Iterator[tuple[str, dict[str, Any]]]:
        files_dir = get_settings().resolved_files_dir
        category_names: dict[tuple[str, str], str | None] = {}
        params = {"filter[kind]": self.quipu_kind, "include": "items"}
        for page in quipu.iter_pages("/invoices", params):
            included = {(x["type"], x["id"]): x for x in page.get("included", [])}
            for invoice in page.get("data", []):
                refs = ((invoice.get("relationships") or {}).get("items") or {}).get("data") or []
                items = [
                    included[(r["type"], r["id"])] for r in refs if (r["type"], r["id"]) in included
                ]
                payload = {**invoice, "items": items}
                payload[ACCOUNT_NAME_KEY] = self._account_name(quipu, invoice, category_names)
                self._download_attachment(quipu, payload, files_dir)
                yield str(invoice["id"]), payload

    @staticmethod
    def _account_name(
        quipu: QuipuClient, invoice: dict, cache: dict[tuple[str, str], str | None]
    ) -> str | None:
        """Nombre de la subcuenta: subcategoría contable de Quipu o, si no tiene, la categoría."""
        relationships = invoice.get("relationships") or {}
        for key in ("accounting_subcategory", "accounting_category"):
            ref = (relationships.get(key) or {}).get("data")
            if not ref:
                continue
            ident = (ref["type"], ref["id"])
            if ident not in cache:
                cache[ident] = quipu.get_resource(*ident).get("attributes", {}).get("name")
            if cache[ident]:
                return cache[ident]
        return None

    def _download_attachment(self, quipu: QuipuClient, payload: dict, files_dir: Path) -> None:
        attrs = payload.get("attributes") or {}
        url = attrs.get("download_pdf_url")
        if not url or attrs.get("stage") == "draft":
            return
        try:
            content, content_type = quipu.download(url)
        except (QuipuError, httpx.HTTPError) as exc:
            payload[ATTACHMENT_ERROR_KEY] = str(exc)
            return
        extension = EXTENSIONS.get(content_type)
        if extension is None:
            payload[ATTACHMENT_ERROR_KEY] = f"tipo de fichero inesperado ({content_type})"
            return

        relative = Path(self.entity_type) / f"{payload['id']}.{extension}"
        (files_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        (files_dir / relative).write_bytes(content)
        number = re.sub(r"[^\w.-]+", "_", attrs.get("number") or str(payload["id"]))
        payload[ATTACHMENT_KEY] = {
            "path": relative.as_posix(),
            "content_type": content_type,
            "filename": f"{number}.{extension}",
        }

    def transform(self, source: dict[str, Any]) -> Transformed:
        raise RecordError(f"Transformación de '{self.label}' pendiente de implementar")

    # --- Carga ---

    def before_load(self, holded: HoldedClient, payloads: list[dict[str, Any]]) -> None:
        """Crea en Holded las subcuentas contables que faltan, con el mismo número que en Quipu.

        Holded asigna «la siguiente libre» bajo un prefijo de 4 dígitos, así que solo se crea
        una subcuenta cuando todas las anteriores existen y no hay ninguna posterior.
        """
        needed = {
            p[ACCOUNT_KEY]["code"]: p[ACCOUNT_KEY].get("name") for p in payloads if ACCOUNT_KEY in p
        }
        for code in sorted(needed, key=int):
            number = int(code)
            if number in holded.accounting_accounts():
                continue
            prefix = number // 10_000
            siblings = sorted(n for n in holded.accounting_accounts() if n // 10_000 == prefix)
            if siblings != list(range(prefix * 10_000, number)):
                raise RecordError(
                    f"Falta la cuenta {code} en Holded y no se puede crear con ese número "
                    "automáticamente: créala a mano en el plan contable y vuelve a cargar"
                )
            holded.create_accounting_account(prefix, needed[code])
            if number not in holded.accounting_accounts():
                raise RecordError(f"Holded no ha creado la cuenta {code} con el número esperado")

    def _prepare(
        self, holded: HoldedClient, payload: dict[str, Any], resolve: IdResolver
    ) -> tuple[dict[str, Any], dict | None, Decimal | None]:
        """Payload listo para Holded (sin claves auxiliares), adjunto y total esperado."""
        body = dict(payload)
        quipu_contact_id = body.pop(QUIPU_CONTACT_KEY)
        attachment = body.pop(ATTACHMENT_KEY, None)
        expected_total = body.pop(EXPECTED_TOTAL_KEY, None)
        account = body.pop(ACCOUNT_KEY, None)

        contact_id = resolve("contacts", quipu_contact_id)
        if contact_id is None:
            raise RecordError(f"El contacto {quipu_contact_id} de Quipu no está migrado a Holded")
        body["contactId"] = contact_id
        if account:
            account_id = holded.accounting_accounts().get(int(account["code"]))
            if account_id is None:
                raise RecordError(f"La cuenta {account['code']} no existe en Holded")
            body["items"] = [{**item, "accountingAccountId": account_id} for item in body["items"]]
        return body, attachment, Decimal(expected_total) if expected_total else None

    def load(self, holded: HoldedClient, payload: dict[str, Any], resolve: IdResolver) -> str:
        body, attachment, expected_total = self._prepare(holded, payload, resolve)
        document_id = holded.create_document(self.holded_doc_type, body)
        # A partir de aquí el documento ya existe en Holded: cualquier fallo debe conservar su id
        if attachment:
            try:
                content = (get_settings().resolved_files_dir / attachment["path"]).read_bytes()
                holded.attach_document_file(
                    self.holded_doc_type,
                    document_id,
                    attachment["filename"],
                    content,
                    attachment["content_type"],
                )
            except Exception as exc:
                raise PartialLoadError(
                    document_id, f"Creada en Holded, pero no se pudo adjuntar el PDF: {exc}"
                ) from exc
        if expected_total is not None:
            self._check_total(holded, document_id, expected_total, "Creada")
        return document_id

    def update(
        self, holded: HoldedClient, target_id: str, payload: dict[str, Any], resolve: IdResolver
    ) -> None:
        document = holded.get_document(self.holded_doc_type, target_id)
        if document.get("draft") is not True:
            raise RecordError(
                "Ya está validada en Holded: no se modifica desde aquí para no generar registros "
                "nuevos en Verifactu. Si hace falta, corrígela a mano en Holded"
            )
        body, _attachment, expected_total = self._prepare(holded, payload, resolve)
        # Solo campos editables. approveDoc nunca: aprobaría la factura y la enviaría a Verifactu
        for key in ("approveDoc", "applyContactDefaults", "invoiceNum", "currency"):
            body.pop(key, None)
        holded.update_document(self.holded_doc_type, target_id, body)
        if expected_total is not None:
            self._check_total(holded, target_id, expected_total, "Actualizada")

    def _check_total(
        self, holded: HoldedClient, document_id: str, expected: Decimal, done: str
    ) -> None:
        try:
            total = holded.get_document(self.holded_doc_type, document_id).get("total")
        except Exception as exc:
            raise PartialLoadError(
                document_id, f"{done} en Holded, pero no se pudo comprobar el total: {exc}"
            ) from exc
        if total is not None and abs(_decimal(total) - expected) > CENT:
            raise PartialLoadError(
                document_id,
                f"{done} en Holded, pero su total ({_money(_decimal(total))} €) no coincide "
                f"con el de Quipu ({_money(expected)} €): revísala",
            )


class InvoicesHandler(DocumentHandler):
    entity_type = "invoices"
    label = "Facturas emitidas"
    quipu_kind = "income"
    holded_doc_type = "invoice"
    updatable = True  # solo borradores: ver DocumentHandler.update

    def transform(self, source: dict[str, Any]) -> Transformed:
        attrs = source.get("attributes") or {}
        rels = source.get("relationships") or {}
        if attrs.get("stage") == "draft" or not attrs.get("number"):
            raise RecordError("Borrador en Quipu (sin número): no está emitida y no se importa")
        if (rels.get("amended_invoice") or {}).get("data"):
            raise RecordError("Factura rectificativa: su importación aún no está implementada")
        if ATTACHMENT_ERROR_KEY in source:
            raise RecordError(
                f"No se pudo descargar el PDF de Quipu ({source[ATTACHMENT_ERROR_KEY]}); "
                "vuelve a extraer"
            )
        if ATTACHMENT_KEY not in source:
            raise RecordError("No hay PDF descargado de esta factura; vuelve a extraer")
        contact_id = ((rels.get("contact") or {}).get("data") or {}).get("id")
        if not contact_id:
            raise RecordError("La factura no tiene contacto en Quipu")
        account_code = attrs.get("accounting_account_code")
        if not account_code:
            raise RecordError("La factura no tiene cuenta contable en Quipu")
        refs = (rels.get("items") or {}).get("data") or []
        items = source.get("items") or []
        if not items or len(items) != len(refs):
            raise RecordError("No se han podido leer todas las líneas de la factura en Quipu")

        country = (attrs.get("recipient_country_code") or "es").upper()
        lines = [self._line(item.get("attributes") or {}, country) for item in items]
        computed = sum((line.total for line in lines), Decimal(0)).quantize(CENT)
        expected = _decimal(attrs.get("total_amount")).quantize(CENT)
        if abs(computed - expected) > CENT:
            raise RecordError(
                f"Las líneas suman {_money(computed)} € y el total en Quipu es {_money(expected)} €"
            )

        account_name = source.get(ACCOUNT_NAME_KEY)
        due_dates = attrs.get("due_dates") or []
        payload = compact(
            {
                "date": _timestamp(attrs["issue_date"]),
                "dueDate": _timestamp(due_dates[0]) if due_dates else None,
                "invoiceNum": attrs["number"],
                "notes": attrs.get("notes"),
                "currency": "eur",
                # Nunca aprobar desde aquí: Holded la enviaría a Verifactu y ya se envió desde Quipu
                "approveDoc": False,
                # Impuestos y condiciones exactamente como en Quipu, no los del contacto en Holded
                "applyContactDefaults": False,
                "items": [line.item for line in lines],
                QUIPU_CONTACT_KEY: contact_id,
                ATTACHMENT_KEY: source[ATTACHMENT_KEY],
                EXPECTED_TOTAL_KEY: str(expected),
                ACCOUNT_KEY: {"code": account_code, "name": account_name},
            }
        )
        stage = VERIFACTU_STAGES.get(attrs.get("stage"), f"Verifactu (Quipu): {attrs.get('stage')}")
        account = f"{account_code} {account_name}" if account_name else account_code
        summary = f"{attrs['number']} · {_money(expected)} € · cuenta {account} · {stage}"
        return Transformed(payload, summary)

    def _line(self, item: dict[str, Any], country: str) -> Line:
        units = _decimal(item.get("quantity"))
        price = _decimal(item.get("unitary_amount"))
        discount = _decimal(item.get("discount_percent"))
        vat = _decimal(item.get("vat_percent"))
        retention = _decimal(item.get("retention_percent"))

        taxes = [self._vat_key(vat, country)]
        if retention:
            key = SALES_RETENTION_KEYS.get(retention)
            if key is None:
                raise RecordError(f"Retención del {retention}% sin equivalente en Holded")
            taxes.append(key)

        base = units * price * (1 - discount / 100)
        holded_item = compact(
            {
                "name": item.get("concept") or "Sin concepto",
                "desc": item.get("description"),
                "units": float(units),
                "subtotal": float(price),  # precio unitario
                "discount": float(discount),
                "tax": _number(vat),  # el PUT de documentos solo documenta `tax`
                "taxes": taxes,
            }
        )
        return Line(holded_item, base * (1 + vat / 100 - retention / 100))

    @staticmethod
    def _vat_key(vat: Decimal, country: str) -> str:
        if vat:
            key = SALES_VAT_KEYS.get(vat)
            if key is None:
                raise RecordError(f"IVA del {vat}% sin equivalente en Holded")
            return key
        # IVA 0%: la causa depende del cliente (igual que la operación fiscal del contacto)
        if country == "ES":
            raise RecordError("Línea con IVA 0% a un cliente español: revisa la causa de exención")
        if vat_prefix(country) in EU_VAT_PREFIXES:
            return "s_iva_intras"  # prestación intracomunitaria de servicios
        return "s_iva_nosujeto"  # servicios a clientes de fuera de la UE


class ExpensesHandler(DocumentHandler):
    entity_type = "expenses"
    label = "Gastos (facturas recibidas)"
    quipu_kind = "expenses"
    holded_doc_type = "purchase"
