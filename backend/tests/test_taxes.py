from app.models import MigrationRun, Record
from app.reports.taxes import SourceDocument, tax_report


def line(
    base, rate="21.0", vat=None, deductible=None, expense_pct="100.0", kind="current", **extra
):
    vat = vat if vat is not None else str(float(base) * float(rate) / 100)
    attrs = {
        "quantity": "1.0",
        "unitary_amount": str(base),
        "discount_amount": "0.0",
        "vat_percent": rate,
        "vat_amount": vat,
        "deductible_vat_amount": deductible if deductible is not None else vat,
        "deductible_expense_percent": expense_pct,
        "retention_amount": "0.0",
        "kind": kind,
        **extra,
    }
    return {"attributes": attrs}


def doc(entity_type, day, *lines, **attrs):
    payload = {"attributes": {"issue_date": day, **attrs}, "items": list(lines)}
    return payload if entity_type is None else SourceDocument(entity_type, payload)


def sale(day, *lines, country="es", tax_id="B1", total="1210.0", stage="approved_aeat"):
    return SourceDocument(
        "invoices",
        doc(
            None,
            day,
            *lines,
            recipient_country_code=country,
            recipient_tax_id=tax_id,
            recipient_name="Cliente",
            total_amount=total,
            total_amount_without_taxes="1000.0",
            stage=stage,
        ),
    )


def purchase(day, *lines, country="es", overrides=None, total="121.0"):
    payload = doc(
        None,
        day,
        *lines,
        issuing_country_code=country,
        issuing_tax_id="A1",
        issuing_name="Proveedor",
        total_amount=total,
    )
    return SourceDocument("expenses", payload, overrides)


def q(report, number):
    return report["quarters"][number - 1]


def test_303_output_and_deductible_vat_by_quarter():
    report = tax_report(
        [
            sale("2026-02-10", line(1000)),
            purchase("2026-03-01", line(100)),
            purchase("2026-03-02", line(100, deductible="10.5", expense_pct="50.0")),  # 50 %
            purchase("2026-03-03", line(4870, kind="asset")),
            sale("2026-05-01", line(500, rate="0.0", vat="0.0"), country="ie", total="500.0"),
        ],
        2026,
    )
    m303 = q(report, 1)["m303"]
    assert m303["output_by_rate"] == [{"rate": "21.00", "base": "1000.00", "vat": "210.00"}]
    assert (m303["input_current_base"], m303["input_current_vat"]) == ("150.00", "31.50")
    assert (m303["input_assets_base"], m303["input_assets_vat"]) == ("4870.00", "1022.70")
    assert m303["result"] == "-844.20"
    assert q(report, 2)["m303"]["no_vat_eu"] == "500.00"
    assert report["indicators"]["m349"] is True


def test_130_is_cumulative_and_excludes_assets_and_supplied_lines():
    report = tax_report(
        [
            sale("2026-01-15", line(1000)),
            purchase("2026-01-20", line(100, deductible="10.5", expense_pct="50.0")),
            purchase("2026-01-21", line(4870, kind="asset")),
            purchase("2026-01-22", line(300), overrides={"supplied_lines": [0]}),
            sale("2026-04-15", line(1000)),
        ],
        2026,
        simplified=False,
    )
    first, second = q(report, 1)["m130"], q(report, 2)["m130"]
    # gasto deducible: (100 base + 10,50 IVA no deducible) × 50 % = 55,25
    assert (first["income"], first["expenses"], first["net"]) == ("1000.00", "55.25", "944.75")
    assert (first["tax_20"], first["to_pay"]) == ("188.95", "188.95")
    assert (second["income"], second["previous_payments"], second["to_pay"]) == (
        "2000.00",
        "188.95",
        "200.00",
    )


def test_347_lists_spanish_counterparts_above_the_threshold():
    sales = [sale(f"2026-0{m}-01", line(1000), tax_id="B99") for m in (1, 2, 3)]
    report = tax_report(sales, 2026)
    assert report["indicators"]["m347"] == [
        {"side": "ventas", "tax_id": "B99", "name": "Cliente", "total": "3630.00"}
    ]


def test_drafts_and_other_years_are_ignored():
    draft = sale("2026-01-01", line(1000), stage="draft")
    report = tax_report([draft, sale("2025-12-31", line(1000))], 2026)
    assert q(report, 1)["m303"]["output_vat"] == "0.00"
    assert report["years"] == [2025]


def test_endpoint_uses_the_latest_extraction_of_each_document(client, session):
    old = MigrationRun(entities=["invoices"])
    new = MigrationRun(entities=["invoices"])
    for run, base in ((old, 1), (new, 1000)):
        session.add(run)
        session.add(
            Record(
                run=run,
                entity_type="invoices",
                source_id="1",
                source_payload=sale("2026-02-01", line(base)).payload,
            )
        )
    session.commit()

    body = client.get("/api/reports/taxes").json()
    assert body["year"] == 2026
    assert body["quarters"][0]["m303"]["output_vat"] == "210.00"


def test_130_in_simplified_modality_subtracts_5_percent_capped_at_2000():
    small = tax_report([sale("2026-01-15", line(1000))], 2026)
    m130 = q(small, 1)["m130"]
    assert (m130["net_before"], m130["hard_to_justify"], m130["net"]) == (
        "1000.00",
        "50.00",
        "950.00",
    )
    assert m130["tax_20"] == "190.00"

    big = tax_report([sale("2026-01-15", line(100000), total="121000.0")], 2026)
    assert q(big, 1)["m130"]["hard_to_justify"] == "2000.00"  # máximo anual
