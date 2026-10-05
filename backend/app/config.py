from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # En Docker se sobrescribe con sqlite:////data/quipu_to_holded.db (volumen)
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'quipu_to_holded.db').as_posix()}"
    # Ficheros descargados de Quipu (PDF de facturas...). Por defecto, junto a la BD
    files_dir: Path | None = None

    quipu_app_id: str = ""
    quipu_app_secret: str = ""
    quipu_base_url: str = "https://getquipu.com"

    holded_api_key: str = ""
    holded_base_url: str = "https://api.holded.com/api"

    # Validación de VAT intracomunitarios (Comisión Europea), API pública sin credenciales
    vies_base_url: str = "https://ec.europa.eu/taxation_customs/vies/rest-api"

    http_timeout: float = 30.0

    @property
    def resolved_files_dir(self) -> Path:
        if self.files_dir:
            return self.files_dir
        return Path(make_url(self.database_url).database or ".").parent / "files"

    @property
    def quipu_configured(self) -> bool:
        return bool(self.quipu_app_id and self.quipu_app_secret)

    @property
    def holded_configured(self) -> bool:
        return bool(self.holded_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
