import httpx
import pytest

from app.importer import files
from app.importer.files import FileFetchError, fetch_document, sniff_content_type, stored_document


@pytest.mark.parametrize(
    ("content", "declared", "expected"),
    [
        (b"%PDF-1.7", "application/pdf; charset=binary", "application/pdf"),
        (b"%PDF-1.7", "binary/octet-stream", "application/pdf"),
        (b"\x89PNG\r\n", "application/octet-stream", "image/png"),
        (b"\xff\xd8\xff\xe0", "", "image/jpeg"),
        (b"<html>", "text/html", None),
    ],
)
def test_sniff_content_type(content, declared, expected):
    assert sniff_content_type(content, declared) == expected


def test_fetch_replaces_previous_document(tmp_path, monkeypatch):
    responses = iter(
        [
            httpx.Response(200, content=b"\x89PNG\r\n", headers={"content-type": "image/png"}),
            httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}),
        ]
    )
    monkeypatch.setattr(files.httpx, "get", lambda *_a, **_kw: next(responses))

    fetch_document(tmp_path, "expenses", "77", "https://s3.test/a.png", 10)
    assert fetch_document(tmp_path, "expenses", "77", "https://s3.test/a.pdf", 10) == (
        "expenses/77.pdf",
        "application/pdf",
        8,
    )
    assert stored_document(tmp_path, "expenses", "77") == ("expenses/77.pdf", "application/pdf")
    assert sorted(p.name for p in (tmp_path / "expenses").iterdir()) == ["77.pdf"]


def test_fetch_rejects_non_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(
        files.httpx, "get", lambda *_a, **_kw: httpx.Response(200, content=b"<html>")
    )
    with pytest.raises(FileFetchError, match="no es un PDF"):
        fetch_document(tmp_path, "expenses", "77", "https://s3.test/x", 10)
