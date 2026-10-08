"""Cuadre de impuestos por trimestre (303 y 130) a partir de los datos extraídos de Quipu.

Es orientativo: sirve para comparar con lo presentado desde Quipu y con lo que calcule Holded.
- 303: IVA repercutido por tipo (facturas emitidas), operaciones sin IVA y IVA soportado
  deducible (`deductible_vat_amount` de cada línea), separando los bienes de inversión.
- 130 (estimación directa, acumulado desde enero): ingresos, gastos deducibles en IRPF
  (base + IVA no deducible, por el % de gasto deducible de cada línea; los bienes de inversión
  no son gasto, sí sus cuotas de amortización), rendimiento, 20 % y retenciones soportadas.
  En estimación directa simplificada se restan los gastos de difícil justificación: 5 % del
  rendimiento neto previo positivo, con un máximo de 2.000 € al año.
Los suplidos marcados en la revisión no cuentan.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.importer.entities.contacts import EU_VAT_PREFIXES, vat_prefix

CENT = Decimal("0.01")
THRESHOLD_347 = Decimal("3005.06")
HARD_TO_JUSTIFY_RATE = Decimal("0.05")
HARD_TO_JUSTIFY_CAP = Decimal(2000)


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except ArithmeticError:
        return Decimal(0)


def _round(value: Decimal) -> str:
    return str(value.quantize(CENT))


def quarter_of(day: str) -> int:
    return (int(day[5:7]) - 1) // 3 + 1


def _country(code: str | None) -> str:
    code = (code or "es").upper()
    if code == "ES":
        return "ES"
    return "UE" if vat_prefix(code) in EU_VAT_PREFIXES else "fuera"


@dataclass
class Quarter:
    output_vat: dict[Decimal, list[Decimal]] = field(
        default_factory=lambda: defaultdict(lambda: [Decimal(0)] * 2)
    )
    no_vat_eu: Decimal = Decimal(0)
    no_vat_other: Decimal = Decimal(0)
    input_current: list[Decimal] = field(default_factory=lambda: [Decimal(0)] * 2)
    input_assets: list[Decimal] = field(default_factory=lambda: [Decimal(0)] * 2)
    income: Decimal = Decimal(0)
    expenses: Decimal = Decimal(0)
    retentions: Decimal = Decimal(0)
    eu_purchases: Decimal = Decimal(0)
    eu_sales: Decimal = Decimal(0)
    purchase_retentions: Decimal = Decimal(0)


@dataclass(frozen=True)
class SourceDocument:
    entity_type: str  # invoices (emitidas), expenses, tickets
    payload: dict[str, Any]
    overrides: dict[str, Any] | None = None


def _lines(payload: dict[str, Any]):
    for index, item in enumerate(payload.get("items") or []):
        attrs = item.get("attributes") or {}
        base = _d(attrs.get("quantity") or 1) * _d(attrs.get("unitary_amount")) - _d(
            attrs.get("discount_amount")
        )
        yield index, attrs, base


def tax_report(
    documents: list[SourceDocument], year: int, simplified: bool = True
) -> dict[str, Any]:
    quarters = {q: Quarter() for q in range(1, 5)}
    totals_347: dict[tuple[str, str], list[Any]] = {}
    years: set[int] = set()

    for doc in documents:
        attrs = doc.payload.get("attributes") or {}
        day = attrs.get("issue_date")
        if not day or (doc.entity_type == "invoices" and attrs.get("stage") == "draft"):
            continue
        years.add(int(day[:4]))
        if int(day[:4]) != year:
            continue
        quarter = quarters[quarter_of(day)]
        supplied = set((doc.overrides or {}).get("supplied_lines") or [])

        if doc.entity_type == "invoices":  # emitidas
            country = _country(attrs.get("recipient_country_code"))
            for _, line, base in _lines(doc.payload):
                rate = _d(line.get("vat_percent"))
                quarter.income += base
                quarter.retentions += _d(line.get("retention_amount"))
                if rate:
                    quarter.output_vat[rate][0] += base
                    quarter.output_vat[rate][1] += _d(line.get("vat_amount"))
                elif country == "UE":
                    quarter.no_vat_eu += base
                else:
                    quarter.no_vat_other += base
            if country == "UE":
                quarter.eu_sales += _d(attrs.get("total_amount_without_taxes"))
            key, name = attrs.get("recipient_tax_id"), attrs.get("recipient_name")
        else:  # gastos y tickets
            country = _country(attrs.get("issuing_country_code"))
            for index, line, base in _lines(doc.payload):
                if index in supplied:
                    continue
                vat, deductible = _d(line.get("vat_amount")), _d(line.get("deductible_vat_amount"))
                vat_rate = _d(line.get("vat_percent"))
                deductible_share = deductible / vat if vat else Decimal(0)
                target = (
                    quarter.input_assets if line.get("kind") == "asset" else quarter.input_current
                )
                if deductible:
                    target[0] += base * deductible_share
                    target[1] += deductible
                if line.get("kind") != "asset":
                    expense_pct = _d(line.get("deductible_expense_percent")) / 100
                    quarter.expenses += (base + vat - deductible) * expense_pct
                quarter.purchase_retentions += _d(line.get("retention_amount"))
                if country == "UE" and not vat_rate:
                    quarter.eu_purchases += base
            key, name = attrs.get("issuing_tax_id"), attrs.get("issuing_name")

        if country == "ES" and key:  # 347: operaciones con españoles (las de la UE van al 349)
            side = "ventas" if doc.entity_type == "invoices" else "compras"
            entry = totals_347.setdefault((side, key), [name, Decimal(0)])
            entry[1] += _d(attrs.get("total_amount"))

    return _render(quarters, totals_347, year, sorted(years), simplified)


def _render(quarters, totals_347, year, years, simplified) -> dict[str, Any]:
    out_quarters = []
    income_acc = expenses_acc = retentions_acc = paid_acc = Decimal(0)
    for number, q in quarters.items():
        output = sum((v[1] for v in q.output_vat.values()), Decimal(0))
        deductible = q.input_current[1] + q.input_assets[1]
        income_acc += q.income
        expenses_acc += q.expenses
        retentions_acc += q.retentions
        net_before = income_acc - expenses_acc
        hard_to_justify = Decimal(0)
        if simplified:  # 5 % del rendimiento previo positivo, máximo 2.000 € al año (acumulado)
            hard_to_justify = min(
                max(net_before, Decimal(0)) * HARD_TO_JUSTIFY_RATE, HARD_TO_JUSTIFY_CAP
            )
        net = net_before - hard_to_justify
        tax_130 = max(net * Decimal("0.20"), Decimal(0))
        to_pay = max(tax_130 - retentions_acc - paid_acc, Decimal(0))
        paid_acc += to_pay
        out_quarters.append(
            {
                "quarter": number,
                "m303": {
                    "output_by_rate": [
                        {"rate": _round(rate), "base": _round(b), "vat": _round(v)}
                        for rate, (b, v) in sorted(q.output_vat.items())
                    ],
                    "output_vat": _round(output),
                    "no_vat_eu": _round(q.no_vat_eu),
                    "no_vat_other": _round(q.no_vat_other),
                    "input_current_base": _round(q.input_current[0]),
                    "input_current_vat": _round(q.input_current[1]),
                    "input_assets_base": _round(q.input_assets[0]),
                    "input_assets_vat": _round(q.input_assets[1]),
                    "input_vat": _round(deductible),
                    "result": _round(output - deductible),
                },
                "m130": {
                    "income": _round(income_acc),
                    "expenses": _round(expenses_acc),
                    "net_before": _round(net_before),
                    "hard_to_justify": _round(hard_to_justify),
                    "net": _round(net),
                    "tax_20": _round(tax_130),
                    "retentions": _round(retentions_acc),
                    "previous_payments": _round(paid_acc - to_pay),
                    "to_pay": _round(to_pay),
                },
                "eu_purchases": _round(q.eu_purchases),
                "eu_sales": _round(q.eu_sales),
                "purchase_retentions": _round(q.purchase_retentions),
            }
        )

    m347 = [
        {"side": side, "tax_id": key, "name": name, "total": _round(total)}
        for (side, key), (name, total) in sorted(totals_347.items(), key=lambda kv: -kv[1][1])
        if total > THRESHOLD_347
    ]
    indicators = {
        "m303": any(
            q["m303"]["output_vat"] != "0.00" or q["m303"]["input_vat"] != "0.00"
            for q in out_quarters
        ),
        "m349": any(q["eu_purchases"] != "0.00" or q["eu_sales"] != "0.00" for q in out_quarters),
        "m347": m347,
        "m111": any(q["purchase_retentions"] != "0.00" for q in out_quarters),
    }
    return {
        "year": year,
        "years": years,
        "simplified": simplified,
        "quarters": out_quarters,
        "indicators": indicators,
    }
