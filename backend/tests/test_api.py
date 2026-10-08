from app.models import MigrationRun, Record, RecordStatus


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_list_entities(client):
    types = [e["type"] for e in client.get("/api/entities").json()]
    assert types == ["accounts", "contacts", "invoices", "expenses", "tickets"]


def test_create_run_orders_entities(client):
    response = client.post("/api/runs", json={"entities": ["invoices", "contacts"]})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created"
    assert body["entities"] == ["contacts", "invoices"]
    assert client.get(f"/api/runs/{body['id']}").json()["id"] == body["id"]


def test_create_run_rejects_unknown_entities(client):
    response = client.post("/api/runs", json={"entities": ["contacts", "unicorns"]})
    assert response.status_code == 422


def test_extract_requires_quipu_credentials(client):
    run_id = client.post("/api/runs", json={"entities": ["contacts"]}).json()["id"]
    assert client.post(f"/api/runs/{run_id}/extract").status_code == 400


def test_missing_run_returns_404(client):
    assert client.get("/api/runs/999").status_code == 404


def _record(session, entity_type: str, status: RecordStatus, items: int = 2) -> Record:
    run = MigrationRun(entities=[entity_type])
    record = Record(
        entity_type=entity_type,
        source_id="77",
        status=status,
        source_payload={"items": [{"id": str(i)} for i in range(items)]},
        target_payload={"x": 1},
        target_id="holded-77",
    )
    run.records = [record]
    session.add(run)
    session.commit()
    return record


def test_marking_supplied_lines_requires_transforming_again(client, session):
    record = _record(session, "expenses", RecordStatus.LOADED)
    url = f"/api/runs/{record.run_id}/records/{record.id}/overrides"

    body = client.put(url, json={"supplied_lines": [1, 1]}).json()

    assert body["overrides"] == {"supplied_lines": [1]}
    assert body["status"] == "extracted"
    assert body["target_payload"] is None
    assert body["target_id"] == "holded-77"  # se conserva: se actualizará, no se duplicará


def test_supplied_lines_are_validated(client, session):
    record = _record(session, "expenses", RecordStatus.TRANSFORMED)
    url = f"/api/runs/{record.run_id}/records/{record.id}/overrides"
    assert client.put(url, json={"supplied_lines": [5]}).status_code == 422


def test_contacts_do_not_accept_supplied_lines(client, session):
    record = _record(session, "contacts", RecordStatus.TRANSFORMED)
    url = f"/api/runs/{record.run_id}/records/{record.id}/overrides"
    assert client.put(url, json={"supplied_lines": [0]}).status_code == 422
