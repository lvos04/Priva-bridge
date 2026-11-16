"""Instellingen uit omgevingsvariabelen (implementatieplan sectie 10)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Amsterdam")


@dataclass
class Settings:
    data_dir: Path
    database_url: str
    catalog_dir: Path
    admin_password: str | None
    secret_key: str
    rate_limit_per_minute: int = 120
    max_upload_bytes: int = 64 * 1024 * 1024
    extra: dict = field(default_factory=dict)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "db"

    def ensure_dirs(self) -> None:
        for path in (self.db_dir, self.raw_dir, self.staging_dir, self.backup_dir):
            path.mkdir(parents=True, exist_ok=True)


def _secret_key(data_dir: Path) -> str:
    """BRIDGE_SECRET_KEY uit de omgeving; anders eenmalig gegenereerd en in /data bewaard.

    Tokenhashes zijn HMAC's met deze sleutel: een gewijzigde sleutel maakt bestaande tokens ongeldig.
    """
    value = os.environ.get("BRIDGE_SECRET_KEY", "").strip()
    if value:
        return value
    path = data_dir / "db" / ".secret_key"
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value)
    path.chmod(0o600)
    return value


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    database_url = os.environ.get("DATABASE_URL", "").strip() or f"sqlite:///{data_dir / 'db' / 'bridge.db'}"
    return Settings(
        data_dir=data_dir,
        database_url=database_url,
        catalog_dir=Path(os.environ.get("BRIDGE_CATALOG_DIR", REPO_ROOT / "catalog")),
        admin_password=os.environ.get("BRIDGE_ADMIN_PASSWORD") or None,
        secret_key=_secret_key(data_dir),
        rate_limit_per_minute=int(os.environ.get("BRIDGE_RATE_LIMIT_PER_MINUTE", "120")),
    )
