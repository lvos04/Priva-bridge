"""Databasemigraties bij het starten, met een back-up vóór elke migratie (sectie 10)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from .config import Settings

log = logging.getLogger("bridge.migrate")


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def current_and_head(url: str) -> tuple[str | None, str]:
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
    return current, head


def sqlite_path(url: str) -> Path | None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        return None
    return Path(parsed.database)


def backup_sqlite(db_path: Path, backup_dir: Path, label: str) -> Path:
    """Consistente kopie via de SQLite-backup-API (ook met WAL)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"bridge-{stamp}-voor-{label}.db"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def upgrade(settings: Settings) -> Path | None:
    """Migreer naar de nieuwste versie. Geeft het pad van de back-up terug (of None)."""
    url = settings.database_url
    db_path = sqlite_path(url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    existed = db_path is not None and db_path.exists() and db_path.stat().st_size > 0
    current, head = current_and_head(url)
    if current == head:
        return None
    backup = None
    if existed:
        backup = backup_sqlite(db_path, settings.backup_dir, head)
        log.info("Back-up vóór migratie %s → %s: %s", current, head, backup)
    elif db_path is None and current is not None:
        log.warning(
            "Geen automatische back-up voor %s; maak zelf een dump vóór de migratie.",
            make_url(url).get_backend_name(),
        )
    command.upgrade(alembic_config(url), "head")
    return backup
