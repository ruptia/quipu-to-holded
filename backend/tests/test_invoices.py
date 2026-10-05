from datetime import UTC, datetime

import pytest

from app.config import get_settings
from app.importer.base import PartialLoadError, RecordError
from app.importer.entities.documents import (
    ACCOUNT_CATALOG_KEY,
    ACCOUNT_KEY,
    ACCOUNT_NAME_KEY,
    ATTACHMENT_ERROR_KEY,
    ATTACHMENT_KEY,
    EXPECTED_TOTAL_KEY,
    QUIPU_CONTACT_KEY,
    InvoicesHandler,
)
from tests.fakes import FakeHolded

ATTACHMENT = {"path": "invoices/1.pdf", "content_type": "application/pdf", "filename": "SER-1.pdf"}
ACCOUNT = {"code": "70500003", "name": "Otros asesoramientos"}


def item(**attrs) -> dict:
    defaults = {
        "concept": "Consultoría",
        "description": "Septiembre",
        "quantity": "1.0",
        "unitary_amount": "1000.0",
        "discount_percent": "0.0",
        "vat_percent": "21.0",
        "retention_percent": "0.0",
    }
    return {"id": "i1", "type": "book_entry_items", "attributes": {**defaults, **attrs}}


def invoice(*items, **attrs) -> dict:
    items = items or (item(),)
    defaults = {
        "number": "SER-1",
        "issue_date": "2026-09-30",
        "due_dates": ["2026-10-30"],
        "notes": "Gracias",
        "stage": "approved_aeat",
        "recipient_country_code": "es",
        "total_amount": "1210.0",
        "accounting_account_code": "70500003",
    }
    return {
        "id": "1",
        "type": "invoices",
        "attributes": {**defaults, **attrs},
        "relationships": {
            "contact": {"data": {"id": "c1", "type": "contacts"}},
            "items": {"data": [{"id": i["id"], "type": i["type"]} for i in items]},
            "amended_invoice": {"data": None},
        },
        "items": list(items),
        ATTACHMENT_KEY: ATTACHMENT,
        ACCOUNT_NAME_KEY: "Otros asesoramientos",
    }


def noon_utc(day: str) -> int:
    return int(datetime.fromisoformat(f"{day}T12:00:00+00:00").astimezone(UTC).timestamp())


# --- Transformación ---


def test_maps_invoice_to_a_holded_draft():
    result = InvoicesHandler().transform(invoice())
    assert result.payload == {
        "date": noon_utc("2026-09-30"),
        "dueDate": noon_utc("2026-10-30"),
        "invoiceNum": "SER-1",
        "notes": "Gracias",
        "currency": "eur",
        "approveDoc": False,
        "applyContactDefaults": False,
        "items": [
            {
                "name": "Consultoría",
                "desc": "Septiembre",
                "units": 1.0,
                "subtotal": 1000.0,
                "discount": 0.0,
                "tax": 21,
                "taxes": ["s_iva_21"],
                ACCOUNT_KEY: ACCOUNT,
            }
        ],
        QUIPU_CONTACT_KEY: "c1",
        ATTACHMENT_KEY: ATTACHMENT,
        EXPECTED_TOTAL_KEY: "1210.00",
    }
    assert result.summary == (
        "SER-1 · 1.210,00 € · cuenta 70500003 Otros asesoramientos · "
        "Verifactu (Quipu): approved_aeat"
    )


def test_discount_and_retention():
    line = item(
        quantity="2.0", unitary_amount="500.0", discount_percent="10.0", retention_percent="15.0"
    )
    # base 900 + IVA 189 - IRPF 135 = 954
    result = InvoicesHandler().transform(invoice(line, total_amount="954.0"))
    assert result.payload["items"][0]["discount"] == 10.0
    assert result.payload["items"][0]["taxes"] == ["s_iva_21", "s_ret_15"]


@pytest.mark.parametrize(("country", "key"), [("ie", "s_iva_intras"), ("us", "s_iva_nosujeto")])
def test_zero_vat_depends_on_the_customer_country(country, key):
    source = invoice(item(vat_percent="0.0"), recipient_country_code=country, total_amount="1000.0")
    assert InvoicesHandler().transform(source).payload["items"][0]["taxes"] == [key]


def test_zero_vat_for_a_spanish_customer_needs_review():
    with pytest.raises(RecordError, match="IVA 0%"):
        InvoicesHandler().transform(invoice(item(vat_percent="0.0"), total_amount="1000.0"))


def test_totals_must_match_quipu():
    with pytest.raises(RecordError, match="Las líneas suman 1.210,00 €"):
        InvoicesHandler().transform(invoice(total_amount="1200.0"))


def test_draft_is_not_imported():
    with pytest.raises(RecordError, match="Borrador"):
        InvoicesHandler().transform(invoice(stage="draft", number=None))


def test_missing_pdf_asks_to_extract_again():
    source = invoice()
    del source[ATTACHMENT_KEY]
    source[ATTACHMENT_ERROR_KEY] = "429"
    with pytest.raises(RecordError, match="No se pudo descargar el PDF"):
        InvoicesHandler().transform(source)


def test_invoice_without_account_needs_review():
    with pytest.raises(RecordError, match="cuenta contable"):
        InvoicesHandler().transform(invoice(accounting_account_code=None))


def test_missing_lines_are_detected():
    source = invoice()
    source["items"] = []
    with pytest.raises(RecordError, match="líneas"):
        InvoicesHandler().transform(source)


# --- Carga ---


@pytest.fixture
def pdf_on_disk():
    path = get_settings().resolved_files_dir / ATTACHMENT["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 test")
    yield
    path.unlink()


def transformed_payload() -> dict:
    return InvoicesHandler().transform(invoice()).payload


def resolve(entity_type, source_id):
    return {"contacts": {"c1": "holded-contact"}}[entity_type].get(source_id)


def test_load_creates_draft_attaches_pdf_and_checks_total(pdf_on_disk):
    holded = FakeHolded(total=1210.0)
    assert InvoicesHandler().load(holded, transformed_payload(), resolve) == "doc-1"

    doc_type, sent = holded.created[0]
    assert doc_type == "invoice"
    assert sent["contactId"] == "holded-contact"
    assert sent["approveDoc"] is False
    assert [i["accountingAccountId"] for i in sent["items"]] == ["acc-70500003"]
    # las claves auxiliares no viajan, ni en el documento ni en las líneas
    assert not any(key.startswith("_") for key in sent)
    assert not any(key.startswith("_") for line in sent["items"] for key in line)
    assert holded.attached == [
        ("invoice", "doc-1", "SER-1.pdf", b"%PDF-1.4 test", "application/pdf")
    ]


def test_attach_failure_keeps_the_holded_id(pdf_on_disk):
    holded = FakeHolded(attach_error=RuntimeError("413"))
    with pytest.raises(PartialLoadError, match="no se pudo adjuntar") as exc:
        InvoicesHandler().load(holded, transformed_payload(), resolve)
    assert exc.value.target_id == "doc-1"


def test_total_mismatch_in_holded_keeps_the_id_and_warns(pdf_on_disk):
    with pytest.raises(PartialLoadError, match="no coincide") as exc:
        InvoicesHandler().load(FakeHolded(total=1000.0), transformed_payload(), resolve)
    assert exc.value.target_id == "doc-1"


def test_contact_must_be_migrated_first():
    with pytest.raises(RecordError, match="no está migrado"):
        InvoicesHandler().load(FakeHolded(), transformed_payload(), lambda *_: None)


def test_update_corrects_drafts_without_approving_them():
    holded = FakeHolded()
    InvoicesHandler().update(holded, "doc-1", transformed_payload(), resolve)

    doc_type, document_id, sent = holded.updated[0]
    assert (doc_type, document_id) == ("invoice", "doc-1")
    assert "approveDoc" not in sent
    assert sent["items"][0]["accountingAccountId"] == "acc-70500003"
    assert holded.attached == []  # el adjunto no se duplica al actualizar


def test_update_never_touches_validated_invoices():
    holded = FakeHolded(draft=False)
    with pytest.raises(RecordError, match="Verifactu"):
        InvoicesHandler().update(holded, "doc-1", transformed_payload(), resolve)
    assert holded.updated == []


# --- Cuentas contables ---


def payload_with_accounts(*accounts: tuple[str, str], catalog: dict | None = None) -> dict:
    items = [{ACCOUNT_KEY: {"code": code, "name": name}} for code, name in accounts]
    return {"items": items, ACCOUNT_CATALOG_KEY: catalog or {}}


def test_before_load_creates_missing_subaccounts_in_order():
    holded = FakeHolded(accounts={70500000: "Prestaciones de servicios"})
    InvoicesHandler().before_load(
        holded,
        [
            payload_with_accounts(("70500003", "Otros asesoramientos")),
            payload_with_accounts(("70500001", "Asesoramiento negocio"), ("70500002", "Formación")),
            payload_with_accounts(("70500000", "Prestación de servicios")),
        ],
    )
    assert holded.created_accounts == [
        (70500001, "Asesoramiento negocio"),
        (70500002, "Formación"),
        (70500003, "Otros asesoramientos"),
    ]


def test_before_load_fills_gaps_with_the_quipu_catalog():
    holded = FakeHolded(accounts={62700000: "Publicidad"})
    catalog = {"62700001": "Atención a los clientes", "62700002": "Promoción online"}
    InvoicesHandler().before_load(
        holded, [payload_with_accounts(("62700003", "Cátering"), catalog=catalog)]
    )
    assert [number for number, _ in holded.created_accounts] == [62700001, 62700002, 62700003]


def test_before_load_refuses_a_gap_it_cannot_fill():
    holded = FakeHolded(accounts={70500000: "Prestaciones de servicios"})
    with pytest.raises(RecordError, match="hace falta antes la 70500001"):
        InvoicesHandler().before_load(holded, [payload_with_accounts(("70500003", "Otros"))])
    assert holded.created_accounts == []


def test_missing_base_account_uses_the_first_subaccount_holded_creates():
    # Holded no crea xxxx0000 por API: crea la xxxx0001
    holded = FakeHolded(accounts={})
    holded.create_accounting_account = lambda prefix, name: holded.accounts.update(
        {prefix * 10_000 + 1: name}
    )
    payload = payload_with_accounts(("63100000", "Otros tributos"))
    InvoicesHandler().before_load(holded, [payload])
    assert holded.accounts == {63100001: "Otros tributos"}


def test_existing_first_subaccount_is_used_for_a_missing_base_account():
    holded = FakeHolded(accounts={63100001: "Otros tributos"})
    InvoicesHandler().before_load(holded, [payload_with_accounts(("63100000", "Otros"))])
    assert holded.created_accounts == []
