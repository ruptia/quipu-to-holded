"""Cliente mínimo del API de Holded (https://developers.holded.com/reference)."""

from typing import Any, Self

import httpx

from app.config import Settings


class HoldedError(RuntimeError):
    pass


class HoldedClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 30.0):
        if not api_key:
            raise HoldedError("Falta la API key de Holded")
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"key": api_key, "Accept": "application/json"},
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(settings.holded_api_key, settings.holded_base_url, settings.http_timeout)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        if response.is_error:
            raise HoldedError(f"{method} {path} → {response.status_code}: {response.text[:300]}")
        return response.json()

    @staticmethod
    def _created_id(body: dict[str, Any]) -> str:
        # Holded responde {"status": 1, "info": "...", "id": "..."}
        if not body.get("id"):
            raise HoldedError(f"Respuesta inesperada de Holded: {body}")
        return str(body["id"])

    def ping(self) -> None:
        self._request("GET", "/invoicing/v1/taxes")

    def create_contact(self, payload: dict[str, Any]) -> str:
        return self._created_id(self._request("POST", "/invoicing/v1/contacts", json=payload))

    def update_contact(self, contact_id: str, payload: dict[str, Any]) -> None:
        """Actualización parcial: Holded solo modifica los campos incluidos en el payload."""
        body = self._request("PUT", f"/invoicing/v1/contacts/{contact_id}", json=payload)
        if body.get("status") != 1:
            raise HoldedError(f"Holded no ha actualizado el contacto {contact_id}: {body}")

    def create_document(self, doc_type: str, payload: dict[str, Any]) -> str:
        """doc_type: invoice, purchase, salesreceipt, creditnote, estimate..."""
        body = self._request("POST", f"/invoicing/v1/documents/{doc_type}", json=payload)
        return self._created_id(body)
