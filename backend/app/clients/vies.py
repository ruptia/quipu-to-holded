"""Validación de VAT intracomunitarios en VIES (API REST pública de la Comisión Europea)."""

from functools import lru_cache

import httpx

from app.config import get_settings


class ViesError(RuntimeError):
    """VIES no ha dado una respuesta definitiva (estado miembro caído, límite de peticiones...)."""


@lru_cache(maxsize=1024)
def check_vat(country_code: str, vat_number: str) -> bool:
    """True si el VAT está dado de alta en VIES; False si no lo está o el formato es inválido.

    `country_code` es el prefijo VAT (EL para Grecia) y `vat_number` el número sin prefijo.
    Solo se cachean respuestas definitivas: si VIES falla se lanza ViesError.
    """
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.vies_base_url}/check-vat-number",
            json={"countryCode": country_code, "vatNumber": vat_number},
            timeout=settings.http_timeout,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ViesError(f"No se pudo consultar VIES: {exc}") from exc

    if "valid" in body:
        return bool(body["valid"])
    errors = {wrapper.get("error") for wrapper in body.get("errorWrappers") or []}
    if "INVALID_INPUT" in errors:
        return False
    detail = ", ".join(sorted(e for e in errors if e)) or f"HTTP {response.status_code}"
    raise ViesError(f"VIES no disponible ahora mismo ({detail}); vuelve a transformar más tarde")
