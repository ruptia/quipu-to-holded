from datetime import date
from decimal import Decimal

import pytest

from app.api import assets as assets_api
from app.assets.amortization import (
    Quota,
    add_months,
    amortization_accounts,
    post_quota,
    quipu_assets,
    schedule,
)
from app.config import Settings, get_settings
from app.main import app
from app.models import MigrationRun, Record
from tests.fakes import FakeHolded

D = Decimal


# --- Cuadro de amortización ---


def test_schedule_of_the_dgx_spark_at_52_percent():
    quotas = schedule("2026-06-29", D("4870"), D(0), D("52"))
    assert len(quotas) == 24
    assert {q.amount for q in quotas[:23]} == {D("211.03")}
    assert quotas[-1].amount == D("16.31")
    assert sum(q.amount for q in quotas) == D("4870")
    assert [q.date for q in quotas[:3]] == ["2026-07-29", "2026-08-29", "2026-09-29"]
    assert quotas[-1].date == "2028-06-29"


def test_residual_value_is_not_amortized():
    quotas = schedule("2026-01-15", D("1200"), D("200"), D("100"))
    assert sum(q.amount for q in quotas) == D("1000")


def test_add_months_keeps_the_day_or_uses_the_last_one():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 12, 29), 2) == date(2027, 2, 28)


def test_amortization_accounts():
    assert amortization_accounts("21700000") == (
        68100000,
        "Amortización del inmovilizado material",
        28170000,
    )
    assert amortization_accounts("20600000")[0::2] == (68000000, 28060000)
    with pytest.raises(ValueError):
        amortization_accounts("62900000")


# --- Asientos en Holded ---


def holded_with_chart():
    return FakeHolded(accounts={21700000: "Equipos", 28170000: "Amortización acumulada EPI"})


def test_post_quota_creates_681_if_missing_and_balances_the_entry():
    holded = holded_with_chart()
    holded.create_accounting_account = lambda prefix, name: holded.accounts.update(
        {prefix * 10_000 + 1: name}  # Holded crea la xxxx0001
    )
    entry_id = post_quota(holded, "DGX Spark", "21700000", Quota(3, "2026-09-29", D("211.03")), 24)

    assert entry_id == "entry-1"
    entry = holded.entries[0]
    assert [
        (line["account"], line.get("debit"), line.get("credit")) for line in entry["lines"]
    ] == [
        (68100001, 211.03, None),
        (28170000, None, 211.03),
    ]
    assert entry["lines"][0]["description"] == "Amortización DGX Spark · cuota 3/24"


# --- Bienes de inversión de Quipu ---


def item(concept, base, kind="current", vat="0", deductible="0", account=None):
    attrs = {
        "concept": concept,
        "quantity": "1.0",
        "unitary_amount": base,
        "kind": kind,
        "vat_amount": vat,
        "deductible_vat_amount": deductible,
        "discount_amount": "0",
    }
    result = {"attributes": attrs}
    if account:
        result["_account"] = {"code": account}
    return result


def document(day, account, *items):
    return {
        "attributes": {"issue_date": day, "accounting_account_code": account},
        "items": list(items),
    }


QUIPU_DOCUMENTS = [
    (
        "1",
        document(
            "2026-06-29",
            "62900003",
            item(
                "NVIDIA DGX Spark - Founders Edition",
                "4870.0",
                kind="asset",
                vat="1022.7",
                deductible="1022.7",
                account="21700000",
            ),
        ),
    ),
    (
        "2",
        document(
            "2026-07-29", "68100000", item("Amortización mensual NVIDIA DGX Spark Fo", "211.03")
        ),
    ),
]


def test_quipu_assets_deduce_the_rate_from_quipu_quotas():
    [found] = quipu_assets(QUIPU_DOCUMENTS)
    assert (found.ref, found.account_code, found.acquisition_date) == (
        "1:0",
        "21700000",
        "2026-06-29",
    )
    assert (found.cost, found.annual_rate) == (D("4870.00"), D("52.00"))


def test_non_deductible_vat_is_part_of_the_cost():
    docs = [
        (
            "9",
            document(
                "2026-01-10",
                "62900000",
                item(
                    "Portátil",
                    "1000",
                    kind="asset",
                    vat="210",
                    deductible="105",
                    account="21700000",
                ),
            ),
        )
    ]
    assert quipu_assets(docs)[0].cost == D("1105.00")


# --- API ---


@pytest.fixture
def holded_api(client, monkeypatch):
    fake = holded_with_chart()
    fake.accounts[68100000] = "Amortización del inmovilizado material"
    monkeypatch.setattr(assets_api.HoldedClient, "from_settings", lambda _settings: fake)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, holded_api_key="k")
    return fake


ASSET = {
    "name": "DGX Spark",
    "account_code": "21700000",
    "acquisition_date": "2026-06-29",
    "cost": "4870",
    "annual_rate": "52",
}


def test_due_quotas_are_created_once_and_the_schedule_gets_locked(client, holded_api):
    asset = client.post("/api/assets", json=ASSET).json()
    assert asset["monthly_amount"] == "211.03"

    report = client.post(f"/api/assets/{asset['id']}/entries", json={"numbers": [1, 2, 1]}).json()
    assert [(r["number"], r["ok"]) for r in report["results"]] == [(1, True), (2, True)]
    assert [q["status"] for q in report["asset"]["quotas"][:2]] == ["creada", "creada"]
    assert report["asset"]["amortized"] == "422.06"
    assert len(holded_api.entries) == 2

    again = client.post(f"/api/assets/{asset['id']}/entries", json={"numbers": [1]}).json()
    assert again["results"][0]["detail"] == "Ya está en Holded"
    assert len(holded_api.entries) == 2

    changed = {**ASSET, "annual_rate": "26"}
    assert client.put(f"/api/assets/{asset['id']}", json=changed).status_code == 409
    assert client.delete(f"/api/assets/{asset['id']}").status_code == 409


def test_future_quotas_cannot_be_created(client, holded_api):
    future = {**ASSET, "acquisition_date": "2099-01-01"}
    asset = client.post("/api/assets", json=future).json()
    report = client.post(f"/api/assets/{asset['id']}/entries", json={"numbers": [1]}).json()
    assert report["results"][0]["ok"] is False
    assert not getattr(holded_api, "entries", [])


def test_quotas_entered_by_hand_can_be_marked(client, holded_api):
    asset = client.post("/api/assets", json=ASSET).json()
    marked = client.put(f"/api/assets/{asset['id']}/entries/1/manual", json={"manual": True}).json()
    assert marked["quotas"][0]["status"] == "manual"
    report = client.post(f"/api/assets/{asset['id']}/entries", json={"numbers": [1]}).json()
    assert report["results"][0]["detail"] == "Ya está en Holded"
    unmarked = client.put(
        f"/api/assets/{asset['id']}/entries/1/manual", json={"manual": False}
    ).json()
    assert unmarked["quotas"][0]["status"] == "vencida"


def test_import_from_quipu_is_idempotent(client, session):
    run = MigrationRun(entities=["expenses", "tickets"])
    session.add(run)
    for source_id, payload in QUIPU_DOCUMENTS:
        entity = "expenses" if source_id == "1" else "tickets"
        session.add(
            Record(run=run, entity_type=entity, source_id=source_id, source_payload=payload)
        )
    session.commit()

    created = client.post("/api/assets/import-quipu").json()
    assert [(a["name"], a["annual_rate"]) for a in created] == [
        ("NVIDIA DGX Spark - Founders Edition", "52.00")
    ]
    assert client.post("/api/assets/import-quipu").json() == []


def test_multiline_quipu_concepts_keep_only_the_first_line_as_name():
    concept = "NVIDIA DGX Spark - Founders Edition\nCortex-A725 - 128 GB\nTFLOPS"
    docs = [
        (
            "1",
            document(
                "2026-06-29", "62900003", item(concept, "4870.0", kind="asset", account="21700000")
            ),
        )
    ]
    assert quipu_assets(docs)[0].name == "NVIDIA DGX Spark - Founders Edition"
