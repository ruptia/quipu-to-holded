import pytest

from app.importer.base import RecordError
from app.importer.entities.chart import CATALOG_KEY, ChartHandler
from tests.fakes import FakeHolded


class FakeQuipu:
    def __init__(self, categories, subcategories):
        self.data = {
            "/accounting_categories": categories,
            "/accounting_subcategories": subcategories,
        }

    def paginate(self, path, params=None):
        return iter(self.data[path])


def category(cid, prefix, name, active=True, kind="expenses"):
    attrs = {"prefix": prefix, "name": name, "active": active, "kind": kind}
    return {"id": cid, "attributes": {**attrs, "accounting_digits_number": 8}}


def subcategory(category_id, suffix, name):
    return {
        "id": f"s{suffix}",
        "attributes": {"suffix": suffix, "name": name},
        "relationships": {"accounting_category": {"data": {"id": category_id}}},
    }


def test_extracts_active_categories_and_all_subcategories_in_number_order():
    quipu = FakeQuipu(
        [
            category("1", 629, "Otros"),
            category("2", 600, "Compras", active=False),
            category("3", 631, "Otros tributos"),
        ],
        [subcategory("1", 2, "SaaS deseables"), subcategory("1", 1, "SaaS necesarias")],
    )
    extracted = list(ChartHandler().extract(quipu))

    assert [code for code, _ in extracted] == ["62900000", "62900001", "62900002", "63100000"]
    payload = dict(extracted)["62900002"]
    assert (payload["name"], payload["level"]) == ("SaaS deseables", "subcategory")
    assert payload[CATALOG_KEY] == {
        "62900000": "Otros",
        "62900001": "SaaS necesarias",
        "62900002": "SaaS deseables",
    }


def test_transform_describes_the_account():
    source = {
        "code": "62900001",
        "name": "SaaS necesarias",
        "kind": "expenses",
        "level": "subcategory",
    }
    result = ChartHandler().transform(source)
    assert result.summary == "62900001 · SaaS necesarias · Gastos · subcuenta de Quipu"


def test_transform_requires_a_numeric_code():
    with pytest.raises(RecordError):
        ChartHandler().transform({"code": None, "name": "X"})


def load(holded, code, name, catalog=None):
    payload = ChartHandler().transform({"code": code, "name": name}).payload
    payload[CATALOG_KEY] = catalog or {}
    return ChartHandler().load(holded, payload, lambda *_: None)


def test_existing_accounts_are_only_linked():
    holded = FakeHolded(accounts={62900000: "Otros servicios"})
    assert load(holded, "62900000", "Otros") == "acc-62900000"
    assert holded.created_accounts == []


def test_missing_subaccount_is_created_with_its_exact_number():
    holded = FakeHolded(accounts={62900000: "Otros servicios"})
    assert load(holded, "62900001", "SaaS necesarias") == "acc-62900001"
    assert holded.created_accounts == [(62900001, "SaaS necesarias")]


def test_missing_base_account_goes_to_the_first_subaccount_of_the_group():
    holded = FakeHolded(accounts={})
    holded_create = holded.create_accounting_account

    def create_without_base(prefix, name):  # Holded crea la xxxx0001 si el grupo está vacío
        holded.accounts[prefix * 10_000] = "temporal"
        result = holded_create(prefix, name)
        del holded.accounts[prefix * 10_000]
        return result

    holded.create_accounting_account = create_without_base
    assert load(holded, "63100000", "Otros tributos") == "acc-63100001"
    assert holded.accounts == {63100001: "Otros tributos"}
