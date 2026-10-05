"""Cliente mínimo del API de Quipu.

Autenticación OAuth2 (client credentials) y respuestas en formato JSON:API.
Paginación con page[number] y meta.pagination_info. El API limita a 5 peticiones por
ventana (cabeceras ratelimit-*): ante un 429 se espera y se reintenta.
"""

import time
from collections.abc import Iterator
from typing import Any, Self

import httpx

from app.config import Settings

MAX_RETRIES = 6


class QuipuError(RuntimeError):
    pass


class QuipuClient:
    ACCEPT = "application/vnd.quipu.v1+json"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        base_url: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not (app_id and app_secret):
            raise QuipuError("Faltan las credenciales de Quipu")
        self._auth = (app_id, app_secret)
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"Accept": self.ACCEPT},
            transport=transport,
        )
        self._authenticated = False

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            settings.quipu_app_id,
            settings.quipu_app_secret,
            settings.quipu_base_url,
            settings.http_timeout,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._http.close()

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Petición con reintentos cuando Quipu responde 429 (límite de peticiones)."""
        for attempt in range(MAX_RETRIES):
            response = self._http.request(method, url, **kwargs)
            if response.status_code != 429:
                return response
            wait = response.headers.get("retry-after") or response.headers.get("ratelimit-reset")
            try:
                delay = max(float(wait or 0), 2**attempt)
            except ValueError:
                delay = 2**attempt
            time.sleep(min(delay, 30))
        return response

    def _authenticate(self) -> None:
        response = self._send(
            "POST",
            "/oauth/token",
            auth=self._auth,
            data={"grant_type": "client_credentials", "scope": "ecommerce"},
        )
        if response.is_error:
            raise QuipuError(f"Autenticación con Quipu fallida ({response.status_code})")
        self._http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        self._authenticated = True

    def _authorized(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not self._authenticated:
            self._authenticate()
        response = self._send(method, url, **kwargs)
        if response.status_code == 401:  # token caducado
            self._authenticate()
            response = self._send(method, url, **kwargs)
        if response.is_error:
            raise QuipuError(f"{method} {url} → {response.status_code}: {response.text[:300]}")
        return response

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._authorized("GET", url, params=params).json()

    def ping(self) -> None:
        self._authenticate()

    def get_resource(self, resource_type: str, resource_id: str) -> dict[str, Any]:
        """Un recurso JSON:API por tipo e id (p. ej. accounting_categories/123)."""
        return self._get(f"/{resource_type}/{resource_id}")["data"]

    def iter_pages(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Devuelve el cuerpo completo de cada página (data + included)."""
        page = 1
        while True:
            body = self._get(path, {**(params or {}), "page[number]": page})
            yield body
            info = (body.get("meta") or {}).get("pagination_info") or {}
            if page >= int(info.get("total_pages") or 1):
                return
            page += 1

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Devuelve cada recurso JSON:API de todas las páginas."""
        for body in self.iter_pages(path, params):
            yield from body.get("data", [])

    def download(self, url: str) -> tuple[bytes, str]:
        """Descarga un fichero (p. ej. el PDF de una factura). Devuelve (contenido, MIME)."""
        response = self._authorized("GET", url, follow_redirects=True)
        content_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, content_type.split(";")[0].strip()
