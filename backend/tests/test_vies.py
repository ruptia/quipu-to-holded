import httpx
import pytest

from app.clients import vies


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    monkeypatch.setattr(vies.time, "sleep", lambda _seconds: None)
    vies.check_vat.cache_clear()
    yield
    vies.check_vat.cache_clear()


def fake_post(monkeypatch, body: dict, status: int = 200) -> list[dict]:
    sent: list[dict] = []

    def post(url, json, timeout):
        sent.append(json)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(vies.httpx, "post", post)
    return sent


@pytest.mark.parametrize("valid", [True, False])
def test_returns_vies_verdict(monkeypatch, valid):
    sent = fake_post(monkeypatch, {"valid": valid})
    assert vies.check_vat("IE", "1234567WA") is valid
    assert sent == [{"countryCode": "IE", "vatNumber": "1234567WA"}]


def test_invalid_input_means_not_valid(monkeypatch):
    fake_post(monkeypatch, {"actionSucceed": False, "errorWrappers": [{"error": "INVALID_INPUT"}]})
    assert vies.check_vat("XX", "123") is False


def test_unavailable_member_state_raises_and_is_not_cached(monkeypatch):
    fake_post(monkeypatch, {"errorWrappers": [{"error": "MS_UNAVAILABLE"}]}, status=500)
    with pytest.raises(vies.ViesError, match="MS_UNAVAILABLE"):
        vies.check_vat("FR", "12345678901")

    fake_post(monkeypatch, {"valid": True})
    assert vies.check_vat("FR", "12345678901") is True


def test_busy_vies_is_retried(monkeypatch):
    responses = iter(
        [
            {"errorWrappers": [{"error": "MS_MAX_CONCURRENT_REQ"}]},
            {"errorWrappers": [{"error": "MS_MAX_CONCURRENT_REQ"}]},
            {"valid": True},
        ]
    )
    calls = []

    def post(url, json, timeout):
        calls.append(json)
        return httpx.Response(200, json=next(responses), request=httpx.Request("POST", url))

    monkeypatch.setattr(vies.httpx, "post", post)
    assert vies.check_vat("FR", "78830399853") is True
    assert len(calls) == 3
