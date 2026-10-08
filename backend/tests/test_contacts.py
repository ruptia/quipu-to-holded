import pytest

from app.importer.base import RecordError
from app.importer.entities.contacts import ContactsHandler
from tests.fakes import FakeVies


def contact(**attrs) -> dict:
    defaults = {"name": "ACME", "is_client": False, "is_supplier": True, "country_code": "es"}
    return {"id": "1", "type": "contacts", "attributes": {**defaults, **attrs}}


def transform(source: dict, vies: FakeVies | None = None):
    return ContactsHandler(vat_checker=vies or FakeVies()).transform(source)


# --- Tipo de contacto ---


@pytest.mark.parametrize(
    ("is_client", "is_supplier", "expected"),
    [(True, False, "client"), (False, True, "creditor"), (False, False, "creditor")],
)
def test_type_from_quipu_flags(is_client, is_supplier, expected):
    result = transform(contact(is_client=is_client, is_supplier=is_supplier))
    assert result.payload["type"] == expected


def test_client_and_supplier_takes_the_bigger_volume():
    both = {"is_client": True, "is_supplier": True}
    sells_more = contact(**both, total_incomes="900.0", total_expenses="100.0")
    buys_more = contact(**both, total_incomes="100.0", total_expenses="900.0")
    assert transform(sells_more).payload["type"] == "client"
    assert transform(buys_more).payload["type"] == "creditor"


# --- Operación fiscal ---


def test_spanish_contact_is_general_and_does_not_hit_vies():
    vies = FakeVies()
    result = transform(contact(tax_id="b-1234567 8", town="Barcelona"), vies)
    assert result.payload == {
        "name": "ACME",
        "code": "B12345678",
        "type": "creditor",
        "taxOperation": "general",
        "billAddress": {"city": "Barcelona", "countryCode": "ES", "country": "España"},
    }
    assert result.summary == "Acreedor · Nacional"
    assert vies.calls == []


@pytest.mark.parametrize("tax_id", ["IE1234567WA", "1234567WA", "ie 1234567 wa"])
def test_eu_contact_with_valid_vat_is_intra(tax_id):
    vies = FakeVies(valid={"IE1234567WA"})
    result = transform(contact(country_code="ie", tax_id=tax_id), vies)
    assert vies.calls == [("IE", "1234567WA")]
    assert result.payload["taxOperation"] == "intra"
    assert result.payload["vatnumber"] == result.payload["code"] == "IE1234567WA"
    assert result.payload["defaults"] == {
        "purchasesTaxes": ["p_iva_adqintras_21"],
        "salesTaxes": ["s_iva_intras"],
    }
    assert result.summary == (
        "Acreedor · Intracomunitario (IE1234567WA, válido en VIES)"
        " · por defecto: adquisición intracomunitaria de servicios"
    )


def test_prefix_comes_from_the_country_not_from_the_tax_id():
    # NIF francés sin prefijo cuya clave (2 primeros caracteres) coincide con el prefijo alemán
    vies = FakeVies(valid={"FRDE123456789"})
    result = transform(contact(country_code="fr", tax_id="DE123456789"), vies)
    assert vies.calls == [("FR", "DE123456789")]
    assert result.payload["vatnumber"] == "FRDE123456789"


@pytest.mark.parametrize("tax_id", ["123456789", "EL123456789", "GR123456789"])
def test_greece_uses_el_prefix(tax_id):
    vies = FakeVies(valid={"EL123456789"})
    result = transform(contact(country_code="gr", tax_id=tax_id), vies)
    assert vies.calls == [("EL", "123456789")]
    assert result.payload["vatnumber"] == "EL123456789"


def test_tax_id_with_another_country_prefix_is_flagged_with_a_hint():
    result = transform(contact(country_code="ie", tax_id="NL123456789B01"), FakeVies())
    assert "taxOperation" not in result.payload
    assert "el NIF empieza por NL pero el país del contacto es IE" in result.summary


def test_eu_contact_without_valid_vat_is_flagged_for_review():
    result = transform(contact(country_code="cy", tax_id="1234567890"), FakeVies())
    assert "taxOperation" not in result.payload
    assert "vatnumber" not in result.payload
    assert "defaults" not in result.payload  # sin VAT válido no se presupone nada
    assert result.payload["code"] == "1234567890"
    assert result.summary.startswith("Acreedor · ⚠ UE: CY1234567890 no es un VAT válido")


def test_eu_contact_without_tax_id_is_flagged_for_review():
    result = transform(contact(country_code="fr", tax_id=None))
    assert "taxOperation" not in result.payload
    assert "⚠" in result.summary


def test_non_eu_contact_is_not_subject():
    vies = FakeVies()
    result = transform(contact(country_code="us", tax_id="US123456789"), vies)
    assert result.payload["taxOperation"] == "nosujeto"
    assert result.payload["defaults"] == {
        "purchasesTaxes": ["p_iva_invsuj"],
        "salesTaxes": ["s_iva_nosujeto"],
    }
    assert result.summary == (
        "Acreedor · Fuera de la UE (no sujeto) · por defecto: inversión del sujeto pasivo"
    )
    assert vies.calls == []


def test_vies_outage_marks_the_record_as_error():
    with pytest.raises(RecordError, match="VIES no disponible"):
        transform(contact(country_code="ie", tax_id="IE1234567WA"), FakeVies(error=True))


def test_transform_requires_name():
    with pytest.raises(RecordError):
        transform(contact(name="  "))


def test_foreign_country_is_sent_with_its_name_or_holded_keeps_spain():
    result = transform(contact(country_code="us", tax_id="US123456789"))
    assert result.payload["billAddress"] == {"countryCode": "US", "country": "Estados Unidos"}
