from app.importer import pipeline
from app.models import MigrationRun, Record, RecordStatus, RunStatus


class FakeHolded:
    def __init__(self):
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass

    def create_contact(self, payload):
        self.created.append(payload)
        return f"holded-{len(self.created)}"

    def update_contact(self, contact_id, payload):
        self.updated.append((contact_id, payload))


def _run_with(session, entities, *records) -> MigrationRun:
    run = MigrationRun(entities=entities, records=list(records))
    session.add(run)
    session.commit()
    return run


def _transformed(entity_type: str, payload: dict, target_id: str | None = None) -> Record:
    return Record(
        entity_type=entity_type,
        source_id="1",
        status=RecordStatus.LOADED if target_id else RecordStatus.TRANSFORMED,
        source_payload={},
        target_payload=payload,
        target_id=target_id,
    )


def test_transform_marks_records_individually(session):
    spanish_contact = {"attributes": {"name": "A", "country_code": "es", "is_supplier": True}}
    run = _run_with(
        session,
        ["contacts", "invoices"],
        Record(entity_type="contacts", source_id="1", source_payload=spanish_contact),
        Record(entity_type="invoices", source_id="9", source_payload={"attributes": {}}),
    )

    pipeline.transform_run(run.id)

    session.expire_all()
    assert run.status == RunStatus.TRANSFORMED
    by_type = {r.entity_type: r for r in run.records}
    assert by_type["contacts"].status == RecordStatus.TRANSFORMED
    assert by_type["contacts"].summary == "Acreedor · Nacional"
    assert by_type["invoices"].status == RecordStatus.ERROR
    assert by_type["invoices"].summary is None


def test_load_creates_new_contacts_and_updates_previously_migrated(session, monkeypatch):
    fake = FakeHolded()
    monkeypatch.setattr(pipeline.HoldedClient, "from_settings", lambda _settings: fake)

    first = _run_with(session, ["contacts"], _transformed("contacts", {"type": "client"}))
    pipeline.load_run(first.id)
    second = _run_with(session, ["contacts"], _transformed("contacts", {"type": "creditor"}))
    pipeline.load_run(second.id)

    session.expire_all()
    assert fake.created == [{"type": "client"}]
    assert fake.updated == [("holded-1", {"type": "creditor"})]
    assert (first.records[0].status, first.records[0].target_id) == (
        RecordStatus.LOADED,
        "holded-1",
    )
    assert (second.records[0].status, second.records[0].target_id) == (
        RecordStatus.UPDATED,
        "holded-1",
    )
    assert second.status == RunStatus.COMPLETED


def test_load_skips_previously_migrated_records_of_non_updatable_entities(session, monkeypatch):
    fake = FakeHolded()
    monkeypatch.setattr(pipeline.HoldedClient, "from_settings", lambda _settings: fake)
    _run_with(session, ["invoices"], _transformed("invoices", {}, target_id="holded-inv"))
    run = _run_with(session, ["invoices"], _transformed("invoices", {}))

    pipeline.load_run(run.id)

    session.expire_all()
    assert (run.records[0].status, run.records[0].target_id) == (
        RecordStatus.SKIPPED,
        "holded-inv",
    )
