import pytest

from app.config import get_settings
from app.importer.base import RecordError
from app.importer.entities.documents import (
    ACCOUNT_CATALOG_KEY,
    ACCOUNT_KEY,
    ACCOUNT_NAME_KEY,
    ATTACHMENT_KEY,
    EXPECTED_TOTAL_KEY,
    ITEM_ACCOUNT_KEY,
    NON_DEDUCTIBLE_SUFFIX,
    QUIPU_CONTACT_KEY,
    ExpensesHandler,
    TicketsHandler,
)
from app.importer.files import save_document
from tests.fakes import FakeHolded


def line(index: int = 0, **attrs) -> dict:
    defaults = {
        "concept": "Luz",
        "description": None,
        "quantity": "1.0",
        "unitary_amount": "100.0",
        "discount_percent": "0.0",
        "vat_percent": "21.0",
        "deductible_vat_percent": "100.0",
        "deductible_expense_percent": "100.0",
        "retention_percent": "0.0",
        "kind": "current",
    }
    return {"id": f"i{index}", "type": "book_entry_items", "attributes": {**defaults, **attrs}}


def expense(*lines, contact: str | None = "c9", **attrs) -> dict:
    lines = lines or (line(),)
    defaults = {
        "number": "F-1",
        "issue_date": "2026-09-01",
        "issuing_name": "Eléctrica SA",
        "issuing_tax_id": "A-1234567 8",
        "accounting_account_code": "62800000",
        "total_amount": "121.0",
    }
    relationships = {"items": {"data": [{"id": i["id"], "type": i["type"]} for i in lines]}}
    if contact:
        relationships["contact"] = {"data": {"id": contact, "type": "contacts"}}
    return {
        "id": "77",
        "type": "invoices",
        "attributes": {**defaults, **attrs},
        "relationships": relationships,
        "items": list(lines),
        ACCOUNT_NAME_KEY: "Suministros",
        ACCOUNT_CATALOG_KEY: {"62800000": "Suministros"},
    }


def pieces(result) -> list[tuple]:
    """(subtotal, impuestos, cuenta, ¿no deducible IRPF?) de cada línea de Holded."""
    return [
        (i["subtotal"], i["taxes"], i[ACCOUNT_KEY]["code"], i[ACCOUNT_KEY]["non_deductible"])
        for i in result.payload["items"]
    ]


def test_fully_deductible_line():
    result = ExpensesHandler().transform(expense())
    assert pieces(result) == [(100.0, ["p_iva_21"], "62800000", False)]
    assert result.payload[QUIPU_CONTACT_KEY] == "c9"
    assert result.payload["approveDoc"] is False
    assert result.payload[EXPECTED_TOTAL_KEY] == "121.00"
    assert result.summary == "F-1 · Eléctrica SA · 121,00 € · ⚠ sin documento"


def test_half_deductible_in_vat_and_irpf_is_split():
    source = expense(line(deductible_vat_percent="50.0", deductible_expense_percent="50.0"))
    result = ExpensesHandler().transform(source)
    assert pieces(result) == [
        (50.0, ["p_iva_21"], "62800000", False),
        (50.0, ["p_iva_nd_21"], "62800000", True),
    ]
    assert result.payload["items"][1]["name"] == "Luz · no deducible (IVA e IRPF)"
    assert "IVA deducible 50 %" in result.summary
    assert "IRPF deducible 50 %" in result.summary


def test_vat_deductible_but_not_irpf():
    source = expense(line(deductible_vat_percent="50.0", deductible_expense_percent="0.0"))
    assert pieces(ExpensesHandler().transform(source)) == [
        (50.0, ["p_iva_21"], "62800000", True),
        (50.0, ["p_iva_nd_21"], "62800000", True),
    ]


def test_irpf_deductible_but_vat_not():
    source = expense(line(vat_percent="10.0", deductible_vat_percent="0.0"), total_amount="110.0")
    result = ExpensesHandler().transform(source)
    assert pieces(result) == [(100.0, ["p_iva_nd_10"], "62800000", False)]
    assert result.payload["items"][0]["name"] == "Luz · IVA no deducible"


def test_zero_vat_line_only_splits_by_irpf():
    source = expense(
        line(vat_percent="0.0", deductible_expense_percent="72.0"), total_amount="100.0"
    )
    assert pieces(ExpensesHandler().transform(source)) == [
        (72.0, ["p_iva_0"], "62800000", False),
        (28.0, ["p_iva_0"], "62800000", True),
    ]


def test_asset_goes_to_its_own_account_with_investment_vat():
    asset = line(kind="asset", unitary_amount="4870.0")
    asset[ITEM_ACCOUNT_KEY] = {"code": "21700000", "name": "Equipos para procesos de información"}
    result = ExpensesHandler().transform(expense(asset, total_amount="5892.7"))
    assert pieces(result) == [(4870.0, ["p_iva_bi_21"], "21700000", False)]
    assert "bien de inversión (21700000)" in result.summary


def test_supplied_lines_are_not_split_and_are_flagged():
    source = expense(
        line(0, deductible_vat_percent="50.0", deductible_expense_percent="50.0"),
        line(1),
        total_amount="242.0",
    )
    result = ExpensesHandler().transform(source, {"supplied_lines": [0]})
    first, second = result.payload["items"]
    assert (first["supplied"], first["taxes"], first[ACCOUNT_KEY]["non_deductible"]) == (
        "Yes",
        ["p_iva_21"],
        False,
    )
    assert "supplied" not in second
    assert "1 suplido(s)" in result.summary


def test_odd_vat_rates_are_rounded_to_holded_rates():
    # Quipu calcula 20,9 % a partir de los importes del ticket
    source = expense(line(vat_percent="20.9"), total_amount="120.9")
    result = ExpensesHandler().transform(source)
    assert pieces(result)[0][1] == ["p_iva_21"]
    assert "IVA 20,9 % → 21 %" in result.summary
    assert result.payload[EXPECTED_TOTAL_KEY] == "121.00"  # lo que debe mostrar Holded


def test_unknown_vat_rate_needs_review():
    with pytest.raises(RecordError, match="IVA del 15 %"):
        ExpensesHandler().transform(expense(line(vat_percent="15.0"), total_amount="115.0"))


def test_totals_must_match_quipu():
    with pytest.raises(RecordError, match="Las líneas suman 121,00 €"):
        ExpensesHandler().transform(expense(total_amount="130.0"))


def test_tickets_without_contact_use_the_issuer():
    result = TicketsHandler().transform(expense(contact=None))
    assert QUIPU_CONTACT_KEY not in result.payload
    assert (result.payload["contactCode"], result.payload["contactName"]) == (
        "A12345678",
        "Eléctrica SA",
    )


@pytest.fixture
def stored_pdf():
    files_dir = get_settings().resolved_files_dir
    path = save_document(files_dir, "expenses", "77", b"%PDF-1.4", "application/pdf")
    yield
    (files_dir / path).unlink()


def test_stored_document_is_attached(stored_pdf):
    result = ExpensesHandler().transform(expense())
    assert result.payload[ATTACHMENT_KEY] == {
        "path": "expenses/77.pdf",
        "content_type": "application/pdf",
        "filename": "F-1.pdf",
    }
    assert "sin documento" not in result.summary


# --- Carga ---


def test_non_deductible_part_goes_to_its_own_subaccount():
    source = expense(line(deductible_vat_percent="50.0", deductible_expense_percent="50.0"))
    payload = ExpensesHandler().transform(source).payload
    holded = FakeHolded(accounts={62800000: "Suministros"})

    ExpensesHandler().before_load(holded, [payload])
    assert holded.created_accounts == [(62800001, "Suministros" + NON_DEDUCTIBLE_SUFFIX)]

    ExpensesHandler().load(holded, payload, lambda _type, _id: "holded-contact")
    doc_type, sent = holded.created[0]
    assert doc_type == "purchase"
    assert [i["accountingAccountId"] for i in sent["items"]] == ["acc-62800000", "acc-62800001"]
    assert not any(key.startswith("_") for line in sent["items"] for key in line)


def test_existing_non_deductible_subaccount_is_reused():
    source = expense(line(deductible_expense_percent="50.0"))
    payload = ExpensesHandler().transform(source).payload
    holded = FakeHolded(
        accounts={62800000: "Suministros", 62800001: "Suministros" + NON_DEDUCTIBLE_SUFFIX}
    )
    ExpensesHandler().before_load(holded, [payload])
    assert holded.created_accounts == []


def test_amortization_quotas_are_not_purchases():
    source = expense(accounting_account_code="68100000", issuing_name="Yo mismo")
    with pytest.raises(RecordError, match="amortización"):
        TicketsHandler().transform(source)


class FakeQuipu:
    def __init__(self, response=(b"%PDF-1.4 gasto", "application/pdf"), error=None):
        self.response, self.error, self.urls = response, error, []

    def download(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.response


def test_expense_with_download_pdf_url_gets_its_pdf_attached():
    files_dir = get_settings().resolved_files_dir
    source = expense(download_pdf_url="https://getquipu.com/invoices/77/download")
    quipu = FakeQuipu()

    ExpensesHandler()._download_attachment(quipu, source, files_dir)
    result = ExpensesHandler().transform(source)

    assert quipu.urls == ["https://getquipu.com/invoices/77/download"]
    assert result.payload[ATTACHMENT_KEY]["path"] == "expenses/77.pdf"
    assert "sin documento" not in result.summary
    (files_dir / "expenses" / "77.pdf").unlink()


def test_failed_pdf_download_is_reported_in_the_review():
    from app.clients import QuipuError

    source = expense(download_pdf_url="https://getquipu.com/invoices/77/download")
    files_dir = get_settings().resolved_files_dir
    ExpensesHandler()._download_attachment(FakeQuipu(error=QuipuError("429")), source, files_dir)

    summary = ExpensesHandler().transform(source).summary
    assert "⚠ no se pudo descargar el PDF de Quipu (429)" in summary
