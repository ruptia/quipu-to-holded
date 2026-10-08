"""Plan contable de Quipu → plan contable de Holded.

Se migran las categorías contables activas de Quipu (las cuentas del PGC que tienes habilitadas)
y todas las subcategorías (tus subcuentas). Holded ya trae el PGC: las que existen solo se
enlazan; las que faltan se crean con las reglas de app.importer.accounts (número exacto para las
subcuentas; las cuentas base, en la primera subcuenta del grupo).
"""

from collections.abc import Iterator
from typing import Any

from app.clients import HoldedClient, QuipuClient
from app.importer.accounts import account_code, ensure_exact_account
from app.importer.base import EntityHandler, IdResolver, RecordError, Transformed

CATALOG_KEY = "_accountCatalog"

KIND_LABELS = {
    "expenses": "Gastos",
    "income": "Ingresos",
    "assets": "Activo",
    "capital": "Patrimonio",
    "financial": "Financieras",
    "creditor_debitor": "Acreedores y deudores",
}


class ChartHandler(EntityHandler):
    entity_type = "accounts"
    label = "Plan contable"

    def extract(self, quipu: QuipuClient) -> Iterator[tuple[str, dict[str, Any]]]:
        categories = {c["id"]: c["attributes"] for c in quipu.paginate("/accounting_categories")}
        rows = [
            {
                "code": account_code(c, None),
                "name": c.get("name"),
                "kind": c.get("kind"),
                "level": "category",
            }
            for c in categories.values()
            if c.get("active")
        ]
        for sub in quipu.paginate("/accounting_subcategories"):
            ref = ((sub.get("relationships") or {}).get("accounting_category") or {}).get("data")
            category = categories.get((ref or {}).get("id"))
            if category:
                rows.append(
                    {
                        "code": account_code(category, sub["attributes"]),
                        "name": sub["attributes"].get("name"),
                        "kind": category.get("kind"),
                        "level": "subcategory",
                    }
                )
        catalog = {row["code"]: row["name"] for row in rows}
        # En orden de número: las subcuentas se crean una tras otra sin huecos
        for row in sorted(rows, key=lambda r: int(r["code"])):
            group = {code: name for code, name in catalog.items() if code[:4] == row["code"][:4]}
            yield row["code"], {**row, CATALOG_KEY: group}

    def transform(
        self, source: dict[str, Any], overrides: dict[str, Any] | None = None
    ) -> Transformed:
        code, name = source.get("code"), (source.get("name") or "").strip()
        if not code or not str(code).isdigit():
            raise RecordError("La cuenta no tiene un número válido en Quipu")
        level = "subcuenta de Quipu" if source.get("level") == "subcategory" else "cuenta del PGC"
        kind = KIND_LABELS.get(source.get("kind") or "", source.get("kind") or "")
        payload = {"code": code, "name": name, CATALOG_KEY: source.get(CATALOG_KEY) or {}}
        return Transformed(payload, " · ".join(p for p in (code, name, kind, level) if p))

    def load(self, holded: HoldedClient, payload: dict[str, Any], resolve: IdResolver) -> str:
        number = ensure_exact_account(
            holded, int(payload["code"]), payload.get("name"), payload.get(CATALOG_KEY) or {}
        )
        return holded.accounting_accounts()[number]
