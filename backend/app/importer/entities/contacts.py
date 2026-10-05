"""Contactos de Quipu → contactos de Holded.

Tipo (Holded admite uno por contacto), a partir de is_client / is_supplier de Quipu:
  - cliente → client
  - proveedor → creditor (acreedor): en Quipu están en la cuenta 410
  - ambos → el de mayor volumen facturado; ninguno → creditor (la mayoría lo son)

Operación fiscal (taxOperation):
  - España → general
  - UE con VAT dado de alta en VIES → intra, y el VAT con prefijo en vatnumber y code.
    El prefijo sale siempre del país del contacto, venga o no escrito en el NIF de Quipu
  - UE sin VAT válido → sin taxOperation y con aviso en el resumen para revisarlo
  - Fuera de la UE → nosujeto
"""

import re
from collections.abc import Callable, Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

from app.clients import HoldedClient, QuipuClient
from app.clients.vies import ViesError, check_vat
from app.importer.base import EntityHandler, IdResolver, RecordError, Transformed, compact

type VatChecker = Callable[[str, str], bool]
"""(prefijo VAT, número sin prefijo) -> ¿dado de alta en VIES?"""

CLIENT, CREDITOR = "client", "creditor"
TYPE_LABELS = {CLIENT: "Cliente", CREDITOR: "Acreedor"}

# Prefijos VAT de los estados miembros (Grecia usa EL, no GR)
# fmt: off
EU_VAT_PREFIXES = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL",
    "ES", "FI", "FR", "HR", "HU", "IE", "IT", "LT", "LU",
    "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
})
# fmt: on


def _amount(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal(0)


def contact_type(attrs: dict[str, Any]) -> str:
    is_client, is_supplier = bool(attrs.get("is_client")), bool(attrs.get("is_supplier"))
    if is_client and is_supplier:
        incomes = _amount(attrs.get("total_incomes"))
        expenses = _amount(attrs.get("total_expenses"))
        return CLIENT if incomes > expenses else CREDITOR
    return CLIENT if is_client else CREDITOR


def clean_tax_id(value: str | None) -> str:
    return re.sub(r"[\s.\-]", "", value or "").upper()


def vat_prefix(country: str) -> str:
    return "EL" if country == "GR" else country


def eu_vat_number(tax_id: str, country: str) -> str:
    """Número VAT sin prefijo. Manda el país del contacto: Quipu guarda el NIF a veces con
    el prefijo del país y a veces sin él (en Grecia puede venir como EL o como GR)."""
    for prefix in dict.fromkeys((vat_prefix(country), country)):
        if tax_id.startswith(prefix):
            return tax_id[len(prefix) :]
    return tax_id


class ContactsHandler(EntityHandler):
    entity_type = "contacts"
    label = "Contactos"
    updatable = True  # corrige en Holded los contactos de cargas anteriores

    def __init__(self, vat_checker: VatChecker = check_vat):
        self._vat_checker = vat_checker

    def extract(self, quipu: QuipuClient) -> Iterator[tuple[str, dict[str, Any]]]:
        for item in quipu.paginate("/contacts"):
            yield str(item["id"]), item

    def transform(
        self, source: dict[str, Any], overrides: dict[str, Any] | None = None
    ) -> Transformed:
        attrs = source.get("attributes") or {}
        name = (attrs.get("name") or "").strip()
        if not name:
            raise RecordError("El contacto no tiene nombre")

        country = (attrs.get("country_code") or "es").upper()
        tax_id = clean_tax_id(attrs.get("tax_id"))
        kind = contact_type(attrs)
        tax_operation, vat_number, fiscal_summary = self._fiscal_profile(country, tax_id)

        payload = compact(
            {
                "name": name,
                "code": vat_number or tax_id,  # NIF/CIF; para intracomunitarios, el VAT
                "vatnumber": vat_number,
                "type": kind,
                "taxOperation": tax_operation,
                "email": attrs.get("email"),
                "phone": attrs.get("phone"),
                "billAddress": {
                    "address": attrs.get("address"),
                    "city": attrs.get("town"),
                    "postalCode": attrs.get("zip_code"),
                    "countryCode": country,
                },
            }
        )
        return Transformed(payload, f"{TYPE_LABELS[kind]} · {fiscal_summary}")

    def _fiscal_profile(self, country: str, tax_id: str) -> tuple[str | None, str | None, str]:
        """(taxOperation, VAT intracomunitario, resumen para la revisión)."""
        if country == "ES":
            return "general", None, "Nacional"
        prefix = vat_prefix(country)
        if prefix not in EU_VAT_PREFIXES:
            return "nosujeto", None, "Fuera de la UE (no sujeto)"
        if not tax_id:
            return None, None, "⚠ UE sin identificador fiscal: revisa la operación fiscal"

        vat = prefix + eu_vat_number(tax_id, country)
        try:
            valid = self._vat_checker(prefix, vat[len(prefix) :])
        except ViesError as exc:
            raise RecordError(str(exc)) from exc
        if valid:
            return "intra", vat, f"Intracomunitario ({vat}, válido en VIES)"

        hint = ""
        if tax_id[:2] != prefix and tax_id[:2] in EU_VAT_PREFIXES:
            hint = f" (el NIF empieza por {tax_id[:2]} pero el país del contacto es {country})"
        return (
            None,
            None,
            f"⚠ UE: {vat} no es un VAT válido en VIES{hint}; revisa la operación fiscal",
        )

    def load(self, holded: HoldedClient, payload: dict[str, Any], resolve: IdResolver) -> str:
        return holded.create_contact(payload)

    def update(
        self, holded: HoldedClient, target_id: str, payload: dict[str, Any], resolve: IdResolver
    ) -> None:
        holded.update_contact(target_id, payload)
