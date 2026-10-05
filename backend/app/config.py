from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # En Docker se sobrescribe con sqlite:////data/quipu_to_holded.db (volumen)
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'quipu_to_holded.db').as_posix()}"

    quipu_app_id: str = ""
    quipu_app_secret: str = ""
    quipu_base_url: str = "https://getquipu.com"

    holded_api_key: str = ""
    holded_base_url: str = "https://api.holded.com/api"

    # Validación de VAT intracomunitarios (Comisión Europea), API pública sin credenciales
    vies_base_url: str = "https://ec.europa.eu/taxation_customs/vies/rest-api"

    http_timeout: float = 30.0

    @property
    def quipu_configured(self) -> bool:
        return bool(self.quipu_app_id and self.quipu_app_secret)

    @property
    def holded_configured(self) -> bool:
        return bool(self.holded_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
