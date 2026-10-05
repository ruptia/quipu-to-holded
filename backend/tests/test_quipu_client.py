import httpx
import pytest

from app.clients import quipu
from app.clients.quipu import QuipuClient


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(quipu.time, "sleep", sleeps.append)
    return sleeps


def client(handler) -> QuipuClient:
    def dispatch(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t"})
        return handler(request)

    return QuipuClient(
        "id", "secret", "https://quipu.test", transport=httpx.MockTransport(dispatch)
    )


def test_paginates_with_page_number_and_meta():
    def handler(request):
        page = int(request.url.params["page[number]"])
        meta = {"pagination_info": {"current_page": page, "total_pages": 3}}
        return httpx.Response(200, json={"data": [{"id": str(page)}], "meta": meta})

    with client(handler) as q:
        assert [r["id"] for r in q.paginate("/invoices", {"filter[kind]": "income"})] == [
            "1",
            "2",
            "3",
        ]


def test_retries_when_rate_limited(no_sleep):
    responses = iter(
        [
            httpx.Response(429, headers={"ratelimit-reset": "2"}),
            httpx.Response(200, json={"data": []}),
        ]
    )

    with client(lambda _request: next(responses)) as q:
        assert q._get("/contacts") == {"data": []}
    assert no_sleep == [2.0]


def test_download_returns_content_and_mime_type():
    def handler(request):
        return httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf; charset=binary"}
        )

    with client(handler) as q:
        assert q.download("https://quipu.test/invoices/1.pdf") == (b"%PDF", "application/pdf")
