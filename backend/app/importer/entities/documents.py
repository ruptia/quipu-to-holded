"""Facturas y tickets de Quipu → documentos de Holded (ventas y compras).

Extracción: listado con include de líneas y categorías contables (las de cada línea llegan
en `included` cuando difieren de las del documento) y el catálogo de subcuentas de Quipu.

Facturas emitidas (Verifactu): ya están registradas en la AEAT desde Quipu. En Holded se
crean SIEMPRE como borrador (approveDoc=false), porque aprobarlas desde el API las enviaría
otra vez a Verifactu. Antes de aprobarlas hay que marcar «No enviar a Verifactu». Por lo
mismo, solo se actualizan documentos que sigan en borrador.

Gastos y tickets: cada línea se reparte según su % de IVA deducible (p_iva_X / p_iva_nd_X) y
su % de gasto deducible en IRPF (cuenta de gasto / subcuenta «no deducible IRPF»). Las líneas
de bien de inversión van a su cuenta de inmovilizado con IVA de bien de inversión. El usuario
marca los suplidos en la revisión (overrides). El documento original no se puede leer del API
de Quipu: llega aparte (POST /api/files/...) y se adjunta si está guardado.

Cuentas: cada línea va a la misma subcuenta que en Quipu. Las que faltan en Holded se crean
antes de cargar, con su número exacto (rellenando huecos con el catálogo de Quipu).
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
from app.importer.entities.contacts import EU_VAT_PREFIXES, clean_tax_id, vat_prefix
from app.importer.files import EXTENSIONS, stored_document

# Claves auxiliares del payload transformado (no se envían a Holded)
QUIPU_CONTACT_KEY = "_quipuContactId"
ATTACHMENT_KEY = "_attachment"
EXPECTED_TOTAL_KEY = "_expectedTotal"
ACCOUNT_KEY = "_account"  # en cada línea: {"code", "name", "non_deductible"}
ACCOUNT_CATALOG_KEY = "_accountCatalog"  # subcuentas de Quipu: código → nombre
# Claves que añade la extracción al payload de Quipu
ATTACHMENT_ERROR_KEY = "_attachmentError"
ACCOUNT_NAME_KEY = "_accountName"
ITEM_ACCOUNT_KEY = "_account"  # en las líneas de Quipu con categoría propia

NON_DEDUCTIBLE_SUFFIX = " – no deducible IRPF"
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

PURCHASE_VAT_KEYS = {
    Decimal(21): "p_iva_21",
    Decimal(10): "p_iva_10",
    Decimal("7.5"): "p_iva_75",
    Decimal(5): "p_iva_5",
    Decimal(4): "p_iva_4",
    Decimal(2): "p_iva_2",
    Decimal(0): "p_iva_0",
}
PURCHASE_NON_DEDUCTIBLE_VAT_KEYS = {
    Decimal(21): "p_iva_nd_21",
    Decimal(10): "p_iva_nd_10",
    Decimal("7.5"): "p_iva_nd_75",
    Decimal(5): "p_iva_nd_5",
    Decimal(4): "p_iva_nd_4",
}
PURCHASE_ASSET_VAT_KEYS = {
    Decimal(21): "p_iva_bi_21",
    Decimal(10): "p_iva_bi_10",
    Decimal(4): "p_iva_bi_4",
}
PURCHASE_RETENTION_KEYS = {Decimal(19): "p_ret_19", Decimal(15): "p_ret_15", Decimal(7): "p_ret_7"}
# Quipu calcula a veces el % de IVA de un ticket a partir de importes (10,02 %, 20,9 %...)
RATE_TOLERANCE = Decimal("0.15")

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


def _percent(value: Decimal) -> str:
    return f"{_number(value)} %".replace(".", ",")


def _timestamp(day: str) -> int:
    """Fecha de Quipu (AAAA-MM-DD) → timestamp Unix a las 12:00 UTC.

    El mediodía UTC cae en el mismo día en cualquier huso horario europeo.
    """
    return int(datetime.combine(date.fromisoformat(day), time(12), tzinfo=UTC).timestamp())


def _account_code(category: dict[str, Any], subcategory: dict[str, Any] | None) -> str:
    """Cuenta de Quipu: prefijo de la categoría + sufijo de la subcategoría (p. ej. 62900003)."""
    prefix = str(category["prefix"])
    digits = int(category.get("accounting_digits_number") or 8)
    suffix = int((subcategory or {}).get("suffix") or 0)
    return f"{prefix}{suffix:0{digits - len(prefix)}d}"


@dataclass(frozen=True)
class Line:
    items: list[dict[str, Any]]  # una línea de Quipu puede dar varias en Holded
    total: Decimal  # base + IVA - retención, para cuadrar con Quipu
    notes: tuple[str, ...] = ()  # avisos para el resumen
    rounding: Decimal = Decimal(0)  # diferencia por redondear un % de IVA (20,9 % → 21 %)


class DocumentHandler(EntityHandler):
    """Base para documentos de Quipu (facturas, tickets) que se convierten en documentos de
    Holded."""

    depends_on = ("contacts",)
    quipu_path: ClassVar[str] = "/invoices"
    quipu_kind: ClassVar[str]
    holded_doc_type: ClassVar[str]
    # Por qué no se tocan los documentos ya aprobados en Holded
    approved_reason: ClassVar[str] = "no se modifica desde aquí"

    # --- Extracción ---

    def extract(self, quipu: QuipuClient) -> Iterator[tuple[str, dict[str, Any]]]:
        catalog = self._quipu_catalog(quipu)
        files_dir = get_settings().resolved_files_dir
        params = {
            "filter[kind]": self.quipu_kind,
            "include": "items,accounting_category,accounting_subcategory,"
            "items.accounting_category,items.accounting_subcategory",
        }
        for page in quipu.iter_pages(self.quipu_path, params):
            included = {(x["type"], x["id"]): x for x in page.get("included", [])}
            for document in page.get("data", []):
                payload = self._with_items(document, included, catalog)
                self._download_attachment(quipu, payload, files_dir)
                yield str(document["id"]), payload

    @staticmethod
    def _quipu_catalog(quipu: QuipuClient) -> dict[str, str]:
        """Todas las cuentas de categorías y subcategorías de Quipu: código → nombre."""
        categories = {c["id"]: c["attributes"] for c in quipu.paginate("/accounting_categories")}
        catalog = {_account_code(c, None): c.get("name") or "" for c in categories.values()}
        for sub in quipu.paginate("/accounting_subcategories"):
            ref = ((sub.get("relationships") or {}).get("accounting_category") or {}).get("data")
            category = categories.get((ref or {}).get("id"))
            if category:
                catalog[_account_code(category, sub["attributes"])] = sub["attributes"].get("name")
        return catalog

    @staticmethod
    def _with_items(document: dict, included: dict, catalog: dict[str, str]) -> dict[str, Any]:
        def resolve(ref: dict | None) -> dict | None:
            return included.get((ref["type"], ref["id"]), {}).get("attributes") if ref else None

        items = []
        for ref in ((document.get("relationships") or {}).get("items") or {}).get("data") or []:
            item = included.get((ref["type"], ref["id"]))
            if item is None:
                continue
            relationships = item.get("relationships") or {}
            category = resolve((relationships.get("accounting_category") or {}).get("data"))
            if category:  # la línea tiene cuenta propia, distinta de la del documento
                subcategory = resolve(
                    (relationships.get("accounting_subcategory") or {}).get("data")
                )
                code = _account_code(category, subcategory)
                item = {**item, ITEM_ACCOUNT_KEY: {"code": code, "name": catalog.get(code)}}
            items.append(item)

        code = (document.get("attributes") or {}).get("accounting_account_code") or ""
        prefixes = {code[:3]} | {
            i[ITEM_ACCOUNT_KEY]["code"][:3] for i in items if ITEM_ACCOUNT_KEY in i
        }
        return {
            **document,
            "items": items,
            ACCOUNT_NAME_KEY: catalog.get(code),
            # Solo las cuentas de los grupos que usa este documento (para rellenar huecos)
            ACCOUNT_CATALOG_KEY: {c: n for c, n in catalog.items() if c[:3] in prefixes},
        }

    def _download_attachment(self, quipu: QuipuClient, payload: dict, files_dir: Path) -> None:
        """Si el documento trae `download_pdf_url`, baja el PDF y lo guarda en el volumen
        (<files_dir>/<entity_type>/<id>.<ext>, donde lo busca también la transformación de los
        gastos). Hoy Quipu solo lo da en las facturas emitidas."""
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
        payload[ATTACHMENT_KEY] = {
            "path": relative.as_posix(),
            "content_type": content_type,
            "filename": f"{_safe_name(attrs.get('number') or payload['id'])}.{extension}",
        }

    def transform(
        self, source: dict[str, Any], overrides: dict[str, Any] | None = None
    ) -> Transformed:
        raise RecordError(f"Transformación de '{self.label}' pendiente de implementar")

    @staticmethod
    def _document_account(source: dict[str, Any]) -> dict[str, Any]:
        code = (source.get("attributes") or {}).get("accounting_account_code")
        if not code:
            raise RecordError("El documento no tiene cuenta contable en Quipu")
        return {"code": code, "name": source.get(ACCOUNT_NAME_KEY)}

    # --- Carga ---

    def before_load(self, holded: HoldedClient, payloads: list[dict[str, Any]]) -> None:
        """Crea en Holded las subcuentas que faltan: primero las de Quipu con su número exacto
        y después las «no deducible IRPF» (que se identifican por nombre)."""
        catalog: dict[str, str] = {}
        exact: dict[int, str | None] = {}
        non_deductible: set[int] = set()
        for payload in payloads:
            catalog.update(payload.get(ACCOUNT_CATALOG_KEY) or {})
            for item in payload.get("items") or []:
                account = item.get(ACCOUNT_KEY)
                if account:
                    exact.setdefault(int(account["code"]), account.get("name"))
                    if account.get("non_deductible"):
                        non_deductible.add(int(account["code"]))

        for number in sorted(exact):
            self._ensure_exact_account(holded, number, exact[number], catalog)
        for number in sorted(non_deductible):
            self._ensure_non_deductible_account(holded, number)

    @staticmethod
    def _ensure_exact_account(
        holded: HoldedClient, number: int, name: str | None, catalog: dict[str, str]
    ) -> None:
        """Holded crea «la siguiente libre» bajo un prefijo de 4 dígitos: solo es seguro crear
        una subcuenta si todas las anteriores existen y no hay ninguna posterior. Los huecos se
        rellenan con las subcuentas del catálogo de Quipu."""
        names = holded.accounting_account_names()
        existing = holded_account_number(holded, number)
        if existing is not None:
            if names[existing].endswith(NON_DEDUCTIBLE_SUFFIX):
                raise RecordError(
                    f"En Holded la cuenta {existing} es una subcuenta «no deducible»: no coincide "
                    "con la {number} de Quipu. Revisa el plan contable"
                )
            return
        prefix, base = number // 10_000, (number // 10_000) * 10_000
        siblings = [n for n in names if n // 10_000 == prefix]
        if number == base and not siblings:
            # Holded no crea cuentas xxxx0000 por API: crea la xxxx0001, que es la que se usará
            holded.create_accounting_account(prefix, name or catalog.get(str(number)))
            if holded_account_number(holded, number) is None:
                raise RecordError(f"Holded no ha creado la cuenta del grupo {prefix} esperada")
            return
        if base not in names or max(siblings) > number:
            raise RecordError(
                f"Falta la cuenta {number} en Holded y no se puede crear con ese número "
                "automáticamente: créala a mano en el plan contable y vuelve a cargar"
            )
        for missing in range(max(siblings) + 1, number + 1):
            missing_name = catalog.get(str(missing))
            if missing == number:
                missing_name = name or missing_name  # sin nombre, Holded usa el de la cuenta padre
            elif not missing_name:
                raise RecordError(
                    f"Para crear la cuenta {number} hace falta antes la {missing}, que no está "
                    "en Quipu: créala a mano en Holded y vuelve a cargar"
                )
            holded.create_accounting_account(prefix, missing_name)
            if missing not in holded.accounting_accounts():
                raise RecordError(f"Holded no ha creado la cuenta {missing} con el número esperado")

    @staticmethod
    def _ensure_non_deductible_account(holded: HoldedClient, number: int) -> None:
        if _non_deductible_account_id(holded, number):
            return
        names = holded.accounting_account_names()
        existing = holded_account_number(holded, number)
        holded.create_accounting_account(number // 10_000, names[existing] + NON_DEDUCTIBLE_SUFFIX)
        if not _non_deductible_account_id(holded, number):
            raise RecordError(f"Holded no ha creado la subcuenta no deducible de {number}")

    def _prepare(
        self, holded: HoldedClient, payload: dict[str, Any], resolve: IdResolver
    ) -> tuple[dict[str, Any], dict | None, Decimal | None]:
        """Payload listo para Holded (sin claves auxiliares), adjunto y total esperado."""
        body = {k: v for k, v in payload.items() if not k.startswith("_")}
        quipu_contact_id = payload.get(QUIPU_CONTACT_KEY)
        if quipu_contact_id:
            contact_id = resolve("contacts", quipu_contact_id)
            if contact_id is None:
                raise RecordError(
                    f"El contacto {quipu_contact_id} de Quipu no está migrado a Holded"
                )
            body["contactId"] = contact_id

        accounts = holded.accounting_accounts()
        items = []
        for item in payload.get("items") or []:
            account = item.get(ACCOUNT_KEY) or payload.get(ACCOUNT_KEY)  # antes: por documento
            item = {k: v for k, v in item.items() if not k.startswith("_")}
            if account:
                number = int(account["code"])
                if account.get("non_deductible"):
                    account_id = _non_deductible_account_id(holded, number)
                else:
                    account_id = accounts.get(holded_account_number(holded, number) or number)
                if account_id is None:
                    raise RecordError(f"La cuenta {number} no existe en Holded")
                item["accountingAccountId"] = account_id
            items.append(item)
        body["items"] = items

        expected = payload.get(EXPECTED_TOTAL_KEY) or payload.get("_quipuTotal")
        return body, payload.get(ATTACHMENT_KEY), Decimal(expected) if expected else None

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
                    document_id, f"Creado en Holded, pero no se pudo adjuntar el documento: {exc}"
                ) from exc
        if expected_total is not None:
            self._check_total(holded, document_id, expected_total, "Creado")
        return document_id

    def update(
        self, holded: HoldedClient, target_id: str, payload: dict[str, Any], resolve: IdResolver
    ) -> None:
        document = holded.get_document(self.holded_doc_type, target_id)
        if document.get("draft") is not True:
            raise RecordError(
                f"Ya está aprobado en Holded: {self.approved_reason}. "
                "Si hace falta, corrígelo a mano en Holded"
            )
        body, _attachment, expected_total = self._prepare(holded, payload, resolve)
        # Solo campos editables. approveDoc nunca: aprobaría el documento
        for key in ("approveDoc", "applyContactDefaults", "invoiceNum", "currency"):
            body.pop(key, None)
        # El adjunto no se vuelve a subir: Holded no dice si ya lo tiene y quedaría duplicado
        holded.update_document(self.holded_doc_type, target_id, body)
        if expected_total is not None:
            self._check_total(holded, target_id, expected_total, "Actualizado")

    def _check_total(
        self, holded: HoldedClient, document_id: str, expected: Decimal, done: str
    ) -> None:
        try:
            total = holded.get_document(self.holded_doc_type, document_id).get("total")
        except Exception as exc:
            raise PartialLoadError(
                document_id, f"{done} en Holded, pero no se pudo comprobar el total: {exc}"
            ) from exc
        if total is not None and abs(_decimal(total) - expected) > CENT * 2:
            raise PartialLoadError(
                document_id,
                f"{done} en Holded, pero su total ({_money(_decimal(total))} €) no coincide "
                f"con el esperado ({_money(expected)} €): revísalo",
            )


def _safe_name(value: Any) -> str:
    return re.sub(r"[^\w.-]+", "_", str(value))


def holded_account_number(holded: HoldedClient, number: int) -> int | None:
    """Cuenta de Holded que corresponde a la de Quipu. Holded no permite crear por API las
    cuentas base (xxxx0000): si la de Quipu es una base que no existe en Holded, se usa la
    primera subcuenta del grupo (xxxx0001)."""
    names = holded.accounting_account_names()
    if number in names:
        return number
    if number % 10_000 == 0 and number + 1 in names:
        return number + 1
    return None


def _non_deductible_account_id(holded: HoldedClient, number: int) -> str | None:
    """Subcuenta «<nombre> – no deducible IRPF» del mismo grupo de 4 dígitos que `number`."""
    names = holded.accounting_account_names()
    existing = holded_account_number(holded, number)
    if existing is None:
        return None
    wanted = names[existing] + NON_DEDUCTIBLE_SUFFIX
    for num, name in names.items():
        if num // 10_000 == number // 10_000 and name == wanted:
            return holded.accounting_accounts()[num]
    return None


class InvoicesHandler(DocumentHandler):
    entity_type = "invoices"
    label = "Facturas emitidas"
    quipu_kind = "income"
    holded_doc_type = "invoice"
    updatable = True  # solo borradores: ver DocumentHandler.update
    approved_reason = "no se modifica desde aquí para no generar registros nuevos en Verifactu"

    def transform(
        self, source: dict[str, Any], overrides: dict[str, Any] | None = None
    ) -> Transformed:
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
        document_account = self._document_account(source)
        refs = (rels.get("items") or {}).get("data") or []
        items = source.get("items") or []
        if not items or len(items) != len(refs):
            raise RecordError("No se han podido leer todas las líneas de la factura en Quipu")

        country = (attrs.get("recipient_country_code") or "es").upper()
        lines = [
            self._line(
                item.get("attributes") or {},
                country,
                item.get(ITEM_ACCOUNT_KEY) or document_account,
            )
            for item in items
        ]
        computed = sum((line.total for line in lines), Decimal(0)).quantize(CENT)
        expected = _decimal(attrs.get("total_amount")).quantize(CENT)
        if abs(computed - expected) > CENT:
            raise RecordError(
                f"Las líneas suman {_money(computed)} € y el total en Quipu es {_money(expected)} €"
            )

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
                "items": [item for line in lines for item in line.items],
                QUIPU_CONTACT_KEY: contact_id,
                ATTACHMENT_KEY: source[ATTACHMENT_KEY],
                EXPECTED_TOTAL_KEY: str(expected),
                ACCOUNT_CATALOG_KEY: source.get(ACCOUNT_CATALOG_KEY),
            }
        )
        stage = VERIFACTU_STAGES.get(attrs.get("stage"), f"Verifactu (Quipu): {attrs.get('stage')}")
        name = document_account["name"]
        account = f"{document_account['code']} {name}" if name else document_account["code"]
        summary = f"{attrs['number']} · {_money(expected)} € · cuenta {account} · {stage}"
        return Transformed(payload, summary)

    def _line(self, item: dict[str, Any], country: str, account: dict[str, Any]) -> Line:
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
                ACCOUNT_KEY: {"code": account["code"], "name": account.get("name")},
            }
        )
        return Line([holded_item], base * (1 + vat / 100 - retention / 100))

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


class PurchaseHandler(DocumentHandler):
    """Gastos de Quipu (facturas recibidas o tickets) → compras de Holded."""

    quipu_kind = "expenses"
    holded_doc_type = "purchase"
    updatable = True  # solo borradores
    overridable = frozenset({"supplied_lines"})
    external_documents = True
    approved_reason = "no se modifica desde aquí"

    def transform(
        self, source: dict[str, Any], overrides: dict[str, Any] | None = None
    ) -> Transformed:
        attrs = source.get("attributes") or {}
        rels = source.get("relationships") or {}
        if not attrs.get("issue_date"):
            raise RecordError("El gasto no tiene fecha en Quipu")
        refs = (rels.get("items") or {}).get("data") or []
        items = source.get("items") or []
        if not items or len(items) != len(refs):
            raise RecordError("No se han podido leer todas las líneas del gasto en Quipu")
        supplied = set((overrides or {}).get("supplied_lines") or [])
        document_account = self._document_account(source)
        accounts = [item.get(ITEM_ACCOUNT_KEY) or document_account for item in items]
        if any(account["code"].startswith("68") for account in accounts):
            # Quipu registra las cuotas de amortización como gastos; en Holded las genera el
            # módulo de activos a partir del bien: importarlas además las duplicaría
            raise RecordError(
                "Cuota de amortización (cuenta 68x): no es una compra y no se importa. Da de "
                "alta el bien en Holded (Activos) y Holded generará las amortizaciones"
            )

        lines = [
            self._line(
                item.get("attributes") or {},
                item.get(ITEM_ACCOUNT_KEY) or document_account,
                supplied=index in supplied,
            )
            for index, item in enumerate(items)
        ]
        computed = sum((line.total for line in lines), Decimal(0))
        expected = _decimal(attrs.get("total_amount"))
        # Si se ha redondeado un % de IVA (20,9 % → 21 %), el total cambia en esa diferencia
        tolerance = CENT * 2 + sum((abs(line.rounding) for line in lines), Decimal(0))
        if abs(computed - expected) > tolerance:
            raise RecordError(
                f"Las líneas suman {_money(computed)} € y el total en Quipu es {_money(expected)} €"
            )

        contact: dict[str, Any] = {}
        contact_id = ((rels.get("contact") or {}).get("data") or {}).get("id")
        if contact_id:
            contact[QUIPU_CONTACT_KEY] = contact_id
        else:  # los tickets no tienen contacto: Holded lo busca por NIF o lo crea
            code, name = clean_tax_id(attrs.get("issuing_tax_id")), attrs.get("issuing_name")
            if not (code or name):
                raise RecordError("El gasto no tiene proveedor (ni contacto ni emisor) en Quipu")
            contact = {"contactCode": code, "contactName": name}

        stored = stored_document(
            get_settings().resolved_files_dir, self.entity_type, str(source["id"])
        )
        attachment = None
        if stored:
            path, content_type = stored
            number = _safe_name(attrs.get("number") or source["id"])
            attachment = {
                "path": path,
                "content_type": content_type,
                "filename": f"{number}.{EXTENSIONS[content_type]}",
            }

        due_dates = attrs.get("due_dates") or []
        payload = compact(
            {
                "date": _timestamp(attrs["issue_date"]),
                "dueDate": _timestamp(due_dates[0]) if due_dates else None,
                "invoiceNum": attrs.get("number"),
                "notes": attrs.get("notes"),
                "currency": "eur",
                "approveDoc": False,
                "applyContactDefaults": False,
                "items": [item for line in lines for item in line.items],
                **contact,
                ATTACHMENT_KEY: attachment,
                EXPECTED_TOTAL_KEY: str(computed.quantize(CENT)),
                ACCOUNT_CATALOG_KEY: source.get(ACCOUNT_CATALOG_KEY),
            }
        )
        notes = [note for line in lines for note in line.notes]
        if supplied:
            notes.append(f"{len(supplied)} suplido(s)")
        if ATTACHMENT_ERROR_KEY in source and not attachment:
            notes.append(f"⚠ no se pudo descargar el PDF de Quipu ({source[ATTACHMENT_ERROR_KEY]})")
        elif not attachment:
            notes.append("⚠ sin documento")
        parts = [
            attrs.get("number") or "s/n",
            attrs.get("issuing_name") or "",
            f"{_money(expected)} €",
            *dict.fromkeys(notes),
        ]
        return Transformed(payload, " · ".join(p for p in parts if p))

    @staticmethod
    def _rate(raw: Decimal) -> Decimal:
        rate = min(PURCHASE_VAT_KEYS, key=lambda r: abs(r - raw))
        if abs(rate - raw) > RATE_TOLERANCE:
            raise RecordError(f"IVA del {_percent(raw)} sin equivalente en Holded")
        return rate

    def _line(self, item: dict[str, Any], account: dict[str, Any], supplied: bool) -> Line:
        concept = item.get("concept") or "Sin concepto"
        units = _decimal(item.get("quantity")) or Decimal(1)
        price = _decimal(item.get("unitary_amount"))
        discount = _decimal(item.get("discount_percent"))
        raw_rate = _decimal(item.get("vat_percent"))
        rate = self._rate(raw_rate)
        vat_deductible = _decimal(item.get("deductible_vat_percent")) / 100
        expense_deductible = _decimal(item.get("deductible_expense_percent")) / 100
        retention = _decimal(item.get("retention_percent"))
        is_asset = item.get("kind") == "asset"
        base = units * price * (1 - discount / 100)

        notes: list[str] = []
        rounding = base * (rate - raw_rate) / 100
        if rate != raw_rate:
            notes.append(f"IVA {_percent(raw_rate)} → {_percent(rate)}")

        retention_keys = []
        if retention:
            key = PURCHASE_RETENTION_KEYS.get(retention)
            if key is None:
                raise RecordError(f"Retención del {_percent(retention)} sin equivalente en Holded")
            retention_keys = [key]

        deductible_key = PURCHASE_VAT_KEYS[rate]
        non_deductible_key = PURCHASE_NON_DEDUCTIBLE_VAT_KEYS.get(rate)
        if is_asset:
            deductible_key = (
                PURCHASE_ASSET_VAT_KEYS.get(rate, deductible_key) if rate else deductible_key
            )
            notes.append(f"bien de inversión ({account['code']})")

        # (fracción, clave de IVA, ¿cuenta no deducible en IRPF?, sufijo del concepto)
        if supplied:
            pieces = [(Decimal(1), deductible_key, False, "")]
        elif rate == 0:  # sin IVA: solo cuenta la deducibilidad en IRPF
            pieces = [
                (expense_deductible, deductible_key, False, ""),
                (1 - expense_deductible, deductible_key, True, " · no deducible IRPF"),
            ]
        else:
            if vat_deductible < 1 and non_deductible_key is None:
                raise RecordError(
                    f"IVA no deducible del {_percent(rate)} sin equivalente en Holded"
                )
            if is_asset:  # la deducibilidad en IRPF de un bien llega por la amortización
                expense_deductible = Decimal(1)
            both = min(vat_deductible, expense_deductible)
            pieces = [
                (both, deductible_key, False, ""),
                (vat_deductible - both, deductible_key, True, " · no deducible IRPF"),
                (expense_deductible - both, non_deductible_key, False, " · IVA no deducible"),
                (
                    1 - max(vat_deductible, expense_deductible),
                    non_deductible_key,
                    True,
                    " · no deducible (IVA e IRPF)",
                ),
            ]
        pieces = [p for p in pieces if p[0] > 0]
        if vat_deductible < 1 and rate and not supplied:
            notes.append(f"IVA deducible {_percent(vat_deductible * 100)}")
        if expense_deductible < 1 and not supplied:
            notes.append(f"IRPF deducible {_percent(expense_deductible * 100)}")

        holded_items = []
        assigned = Decimal(0)
        for index, (fraction, tax_key, non_deductible, suffix) in enumerate(pieces):
            # La última parte se lleva el resto para que la suma sea exacta
            subtotal = (
                price - assigned
                if index == len(pieces) - 1
                else (price * fraction).quantize(Decimal("0.000001"))
            )
            assigned += subtotal
            holded_items.append(
                compact(
                    {
                        "name": concept + suffix,
                        "desc": item.get("description"),
                        "units": float(units),
                        "subtotal": float(subtotal),
                        "discount": float(discount),
                        "tax": _number(rate),
                        "taxes": [tax_key, *retention_keys],
                        "supplied": "Yes" if supplied else None,
                        ACCOUNT_KEY: {
                            "code": account["code"],
                            "name": account.get("name"),
                            "non_deductible": non_deductible,
                        },
                    }
                )
            )
        total = base * (1 + rate / 100 - retention / 100)
        return Line(holded_items, total, tuple(notes), rounding)


class ExpensesHandler(PurchaseHandler):
    entity_type = "expenses"
    label = "Gastos (facturas recibidas)"
    quipu_path = "/invoices"


class TicketsHandler(PurchaseHandler):
    entity_type = "tickets"
    label = "Tickets de gasto"
    quipu_path = "/simplified_invoices"
