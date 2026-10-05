def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_list_entities(client):
    types = [e["type"] for e in client.get("/api/entities").json()]
    assert types == ["contacts", "invoices", "expenses"]


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
