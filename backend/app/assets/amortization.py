"""Amortización de bienes de inversión con asientos en Holded.

Sustituye al módulo de activos de Holded (de pago): calcula el cuadro de amortización lineal
mensual de cada activo y crea cada cuota como asiento contable:

    681 Amortización del inmovilizado material      (debe)
    281x Amortización acumulada del inmovilizado      (haber)

(680 / 280x para el inmovilizado intangible). Las cuotas caen el mismo día de cada mes desde el
mes siguiente al alta, como hacía Quipu. El coeficiente es el del activo: en estimación directa
simplificada, el de la tabla simplificada (equipos informáticos, 26 %), multiplicado por 2 si se
aplica la amortización acelerada de empresa de reducida dimensión.
"""

import calendar
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from app.clients import HoldedClient
from app.importer.accounts import ensure_exact_account, holded_account_number
from app.importer.base import RecordError

CENT = Decimal("0.01")
TAG = "amortizacion"


@dataclass(frozen=True)
class Quota:
    number: int
    date: str  # AAAA-MM-DD
    amount: Decimal


def decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Importe no válido: {value!r}") from exc


def add_months(day: date, months: int) -> date:
    """Mismo día n meses después (el último día del mes si ese no existe: 31 → 30 o 28/29)."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def monthly_amount(cost: Decimal, residual: Decimal, annual_rate: Decimal) -> Decimal:
    return ((cost - residual) * annual_rate / 100 / 12).quantize(CENT)


def schedule(acquisition_date: str, cost: Decimal, residual: Decimal, rate: Decimal) -> list[Quota]:
    """Cuadro lineal mensual. La última cuota es el resto hasta completar lo amortizable."""
    amortizable = cost - residual
    monthly = monthly_amount(cost, residual, rate)
    if amortizable <= 0 or monthly <= 0:
        return []
    start = date.fromisoformat(acquisition_date)
    quotas, total = [], Decimal(0)
    while total < amortizable:
        amount = min(monthly, amortizable - total)
        quotas.append(
            Quota(len(quotas) + 1, add_months(start, len(quotas) + 1).isoformat(), amount)
        )
        total += amount
    return quotas


def amortization_accounts(account_code: str) -> tuple[int, str, int]:
    """(cuenta de gasto, su nombre, cuenta de amortización acumulada) de un inmovilizado.

    21x material → 681 / 281x;  20x intangible → 680 / 280x  (p. ej. 217 → 681 / 2817).
    """
    group = str(account_code)[:3]
    if re.fullmatch(r"21\d", group):
        return 68100000, "Amortización del inmovilizado material", int(f"281{group[2]}0000")
    if re.fullmatch(r"20\d", group):
        return 68000000, "Amortización del inmovilizado intangible", int(f"280{group[2]}0000")
    raise ValueError(f"La cuenta {account_code} no es de inmovilizado (20x o 21x)")


def _timestamp(day: str) -> int:
    return int(datetime.combine(date.fromisoformat(day), time(12), tzinfo=UTC).timestamp())


def post_quota(holded: HoldedClient, name: str, account_code: str, quota: Quota, total: int) -> str:
    """Crea en Holded el asiento de una cuota y devuelve su id."""
    expense, expense_name, accumulated = amortization_accounts(account_code)
    # La 681/680 puede no existir en Holded: se crea (en la xxxx0001, como cualquier cuenta base)
    expense_number = ensure_exact_account(holded, expense, expense_name, {})
    accumulated_number = holded_account_number(holded, accumulated)
    if accumulated_number is None:
        raise RecordError(
            f"Falta en Holded la cuenta de amortización acumulada {accumulated}: créala en el "
            "plan contable"
        )
    description = f"Amortización {name} · cuota {quota.number}/{total}"
    amount = float(quota.amount)
    tags = [TAG]
    lines = [
        {"account": expense_number, "debit": amount, "description": description, "tags": tags},
        {"account": accumulated_number, "credit": amount, "description": description, "tags": tags},
    ]
    return holded.create_entry(_timestamp(quota.date), lines, notes=description)


# --- Bienes de inversión de Quipu ---


@dataclass(frozen=True)
class QuipuAsset:
    ref: str  # «<id del gasto>:<línea>»
    name: str
    account_code: str
    acquisition_date: str
    cost: Decimal
    annual_rate: Decimal | None  # deducido de las cuotas de amortización de Quipu, si las hay


def quipu_assets(documents: Iterable[tuple[str, dict[str, Any]]]) -> list[QuipuAsset]:
    """Líneas de bien de inversión (kind=asset) de los gastos extraídos de Quipu.

    El coeficiente se deduce de las cuotas que Quipu registra como gasto en la 68x cuyo concepto
    menciona el bien: cuota mensual × 12 / coste.
    """
    documents = list(documents)
    quotas: list[tuple[str, Decimal]] = []  # (concepto, importe) de las cuotas de Quipu
    found = []
    for source_id, payload in documents:
        attrs = payload.get("attributes") or {}
        for index, item in enumerate(payload.get("items") or []):
            line = item.get("attributes") or {}
            account = (item.get("_account") or {}).get("code") or attrs.get(
                "accounting_account_code"
            )
            base = decimal(line.get("quantity") or 1) * decimal(line.get("unitary_amount") or 0)
            if str(account).startswith("68"):
                quotas.append((line.get("concept") or "", base))
            elif line.get("kind") == "asset" and attrs.get("issue_date"):
                vat = decimal(line.get("vat_amount") or 0)
                deductible = decimal(line.get("deductible_vat_amount") or 0)
                cost = base - decimal(line.get("discount_amount") or 0) + vat - deductible
                found.append(
                    (
                        f"{source_id}:{index}",
                        line.get("concept") or "Bien de inversión",
                        str(account),
                        attrs["issue_date"],
                        cost,
                    )
                )

    assets = []
    for ref, name, account, day, cost in found:
        key = " ".join(re.findall(r"\w+", name.upper())[:3])
        matching = [amount for concept, amount in quotas if key and key in concept.upper()]
        rate = (matching[0] * 12 / cost * 100).quantize(CENT) if matching and cost else None
        # El concepto de Quipu puede traer varias líneas de descripción: basta la primera
        title = name.strip().splitlines()[0].strip()
        assets.append(QuipuAsset(ref, title, account, day, cost.quantize(CENT), rate))
    return assets
