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
        self._accounts: dict[int, dict[str, str]] | None = None

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
        """doc_type: invoice, purchase, salesreceipt, creditnote, estimate...

        Ojo: con approveDoc=true Holded aprueba la factura y la envía a Verifactu (AEAT).
        """
        body = self._request("POST", f"/invoicing/v1/documents/{doc_type}", json=payload)
        return self._created_id(body)

    def get_document(self, doc_type: str, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/invoicing/v1/documents/{doc_type}/{document_id}")

    def update_document(self, doc_type: str, document_id: str, payload: dict[str, Any]) -> None:
        body = self._request(
            "PUT", f"/invoicing/v1/documents/{doc_type}/{document_id}", json=payload
        )
        if body.get("status") != 1:
            raise HoldedError(f"Holded no ha actualizado el documento {document_id}: {body}")

    def _chart_of_accounts(self) -> dict[int, dict[str, str]]:
        if self._accounts is None:
            accounts = self._request(
                "GET", "/accounting/v1/chartofaccounts", params={"includeEmpty": 1}
            )
            self._accounts = {
                int(a["num"]): {"id": str(a["id"]), "name": str(a.get("name") or "")}
                for a in accounts
            }
        return self._accounts

    def create_entry(self, date: int, lines: list[dict[str, Any]], notes: str | None = None) -> str:
        """Asiento contable. Cada línea: account (número exacto), debit o credit, description,
        tags. Debe y haber tienen que cuadrar. Devuelve el entryGroupId."""
        payload: dict[str, Any] = {"date": date, "lines": lines}
        if notes:
            payload["notes"] = notes
        body = self._request("POST", "/accounting/v1/entry", json=payload)
        if not body.get("entryGroupId"):
            raise HoldedError(f"Respuesta inesperada al crear el asiento: {body}")
        return str(body["entryGroupId"])

    def accounting_accounts(self) -> dict[int, str]:
        """Número de cuenta (p. ej. 70500000) → id interno de Holded, incluidas las vacías."""
        return {num: account["id"] for num, account in self._chart_of_accounts().items()}

    def accounting_account_names(self) -> dict[int, str]:
        """Número de cuenta → nombre."""
        return {num: account["name"] for num, account in self._chart_of_accounts().items()}

    def create_accounting_account(self, prefix: int, name: str | None) -> str:
        """Crea la siguiente subcuenta libre bajo `prefix` (4 dígitos) y devuelve su id."""
        payload: dict[str, Any] = {"prefix": prefix}
        if name:
            payload["name"] = name
        body = self._request("POST", "/accounting/v1/account", json=payload)
        self._accounts = None  # el plan contable ha cambiado
        if not body.get("accountId"):
            raise HoldedError(f"Respuesta inesperada al crear la cuenta: {body}")
        return str(body["accountId"])

    def attach_document_file(
        self,
        doc_type: str,
        document_id: str,
        filename: str,
        content: bytes,
        content_type: str,
        set_main: bool = True,
    ) -> None:
        self._request(
            "POST",
            f"/invoicing/v1/documents/{doc_type}/{document_id}/attach",
            files={"file": (filename, content, content_type)},
            data={"setMain": "true" if set_main else "false"},
        )
