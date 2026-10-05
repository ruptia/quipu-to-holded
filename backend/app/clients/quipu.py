"""Cliente mínimo del API de Quipu.

Autenticación OAuth2 (client credentials) y respuestas en formato JSON:API.
TODO: contrastar rutas, scope y atributos con la documentación oficial de Quipu.
"""

from collections.abc import Iterator
from typing import Any, Self

import httpx

from app.config import Settings


class QuipuError(RuntimeError):
    pass


class QuipuClient:
    ACCEPT = "application/vnd.quipu.v1+json"

    def __init__(self, app_id: str, app_secret: str, base_url: str, timeout: float = 30.0):
        if not (app_id and app_secret):
            raise QuipuError("Faltan las credenciales de Quipu")
        self._auth = (app_id, app_secret)
        self._http = httpx.Client(
            base_url=base_url, timeout=timeout, headers={"Accept": self.ACCEPT}
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

    def _authenticate(self) -> None:
        response = self._http.post(
            "/oauth/token",
            auth=self._auth,
            data={"grant_type": "client_credentials", "scope": "ecommerce"},
        )
        if response.is_error:
            raise QuipuError(f"Autenticación con Quipu fallida ({response.status_code})")
        self._http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        self._authenticated = True

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._authenticated:
            self._authenticate()
        response = self._http.get(url, params=params)
        if response.status_code == 401:  # token caducado
            self._authenticate()
            response = self._http.get(url, params=params)
        if response.is_error:
            raise QuipuError(f"GET {url} → {response.status_code}: {response.text[:300]}")
        return response.json()

    def ping(self) -> None:
        self._authenticate()

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Devuelve cada recurso JSON:API recorriendo todas las páginas (links.next)."""
        url: str | None = path
        while url:
            body = self._get(url, params)
            yield from body.get("data", [])
            url = (body.get("links") or {}).get("next")
            params = None  # el enlace "next" ya incluye los parámetros
