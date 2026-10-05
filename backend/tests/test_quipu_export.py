import io
import zipfile

import pytest

from app.config import get_settings
from app.importer.files import stored_document
from app.importer.quipu_export import (
    ExpenseRef,
    match_documents,
    parse_export_name,
    verify_document,
    verify_text,
)
from app.models import MigrationRun, Record, RecordStatus


def export_name(index: int, original: str) -> str:
    return f"191284-{index}-1-436760-1791163998-7620261005-2149214-57mirp-{original}"


def expense(quipu_id: int, number: str | None = None, account: str = "62900000") -> ExpenseRef:
    return ExpenseRef(key=quipu_id, quipu_id=quipu_id, number=number, account=account)


def pairs(result) -> list[tuple[str, int, str]]:
    return [(m.file.original, m.expense.quipu_id, m.method) for m in result.matches]


def test_parse_export_name_keeps_dashes_of_the_original_name():
    parsed = parse_export_name(export_name(42, "Invoice-B7IC6QHY-0001.pdf"))
    assert (parsed.index, parsed.original) == (42, "Invoice-B7IC6QHY-0001.pdf")
    assert parse_export_name("factura suelta.pdf") is None


def test_numbers_anchor_and_the_gaps_follow_creation_order():
    files = [
        parse_export_name(export_name(9, "invoice__1_.pdf")),
        parse_export_name(export_name(3, "Factura-33268.pdf")),
        parse_export_name(export_name(7, "invoice.pdf")),
        parse_export_name(export_name(12, "LNKD_INVOICE_789.pdf")),
    ]
    expenses = [expense(300, "INV-789"), expense(100, "33268"), expense(200), expense(250)]
    assert pairs(match_documents(files, expenses)) == [
        ("Factura-33268.pdf", 100, "número"),
        ("invoice.pdf", 200, "orden"),
        ("invoice__1_.pdf", 250, "orden"),
        ("LNKD_INVOICE_789.pdf", 300, "orden"),  # «INV789» no está en el nombre normalizado
    ]


def test_amortizations_have_no_document_and_do_not_break_the_order():
    files = [parse_export_name(export_name(1, "a.jpg")), parse_export_name(export_name(2, "b.jpg"))]
    expenses = [expense(10), expense(20, account="68100000"), expense(30)]
    result = match_documents(files, expenses)
    assert pairs(result) == [("a.jpg", 10, "orden"), ("b.jpg", 30, "orden")]
    assert [e.quipu_id for e in result.without_document] == [20]


def test_gaps_that_do_not_fit_are_left_for_review():
    files = [
        parse_export_name(export_name(1, "a.jpg")),
        parse_export_name(export_name(5, "F-1001.pdf")),
    ]
    expenses = [expense(10), expense(20), expense(30, "F-1001")]
    result = match_documents(files, expenses)
    assert pairs(result) == [("F-1001.pdf", 30, "número")]
    assert [f.original for f in result.unmatched_files] == ["a.jpg"]
    assert [e.quipu_id for e in result.unmatched_expenses] == [10, 20]


def test_anchor_that_contradicts_the_order_is_ignored():
    files = [
        parse_export_name(export_name(1, "Factura-1111.pdf")),
        parse_export_name(export_name(2, "Factura-3333.pdf")),
        parse_export_name(export_name(3, "copia-de-1111.pdf")),  # número repetido fuera de orden
    ]
    expenses = [expense(10, "1111"), expense(20, "3333"), expense(30)]
    assert pairs(match_documents(files, expenses)) == [
        ("Factura-1111.pdf", 10, "número"),
        ("Factura-3333.pdf", 20, "número"),
        ("copia-de-1111.pdf", 30, "orden"),
    ]


@pytest.mark.parametrize(
    ("text", "total", "expected"),
    [
        ("Total factura 1.234,50 €", "1234.5", "importe"),
        ("TOTAL 47.19 EUR", "47.19", "importe"),
        ("Invoice number LNKD-789 amount $22.00", "19.19", "número"),
        ("ELEVEN LABS INC. receipt $22.00", "19.19", "emisor"),
        ("Otra cosa 99,99", "19.19", "no cuadra"),
    ],
)
def test_verify_text(text, total, expected):
    assert verify_text(text, total=total, number="LNKD-789", issuer="Eleven Labs Inc.") == expected


def test_images_cannot_be_verified():
    result = verify_document(b"\xff\xd8\xff", "image/jpeg", total="1", number=None, issuer=None)
    assert result == "sin verificar"


# --- Endpoint ---


def _expense_record(
    run: MigrationRun, quipu_id: int, number: str | None, status=RecordStatus.EXTRACTED
):
    attrs = {
        "number": number,
        "issue_date": "2026-07-01",
        "issuing_name": "Prov",
        "total_amount": "10.0",
        "accounting_account_code": "62900000",
    }
    return Record(
        run=run,
        entity_type="expenses",
        source_id=str(quipu_id),
        status=status,
        source_payload={"id": str(quipu_id), "attributes": attrs},
    )


def test_import_quipu_export_zip_saves_documents_and_reports(client, session):
    run = MigrationRun(entities=["expenses"])
    session.add_all(
        [
            run,
            _expense_record(run, 100, "F-100", RecordStatus.TRANSFORMED),
            _expense_record(run, 200, None),
        ]
    )
    session.commit()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(export_name(1, "F-100.pdf"), b"%PDF-1.4 sin texto")
        z.writestr(export_name(2, "foto.jpg"), b"\xff\xd8\xff\xe0 foto")
        z.writestr("README.txt", b"no es del exportador")

    response = client.post(
        f"/api/runs/{run.id}/documents/quipu-export",
        files=[("files", ("export.zip", archive.getvalue(), "application/zip"))],
    )

    assert response.status_code == 200, response.text
    report = response.json()
    assert [(m["file"], m["source_id"], m["method"]) for m in report["matched"]] == [
        ("F-100.pdf", "100", "número"),
        ("foto.jpg", "200", "orden"),
    ]
    assert report["ignored_files"] == ["README.txt"]
    assert report["reset_to_extracted"] == 1
    files_dir = get_settings().resolved_files_dir
    assert stored_document(files_dir, "expenses", "100") == ("expenses/100.pdf", "application/pdf")
    assert stored_document(files_dir, "expenses", "200") == ("expenses/200.jpg", "image/jpeg")
    for name in ("100.pdf", "200.jpg"):
        (files_dir / "expenses" / name).unlink()
