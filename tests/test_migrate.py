"""Migraties: back-up vóór elke migratie in /data/backups; modellen en migraties lopen gelijk."""

import sqlite3

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from bridge.db import Base
from bridge.migrate import current_and_head, upgrade


def test_nieuwe_database_geen_backup_en_head(settings):
    settings.ensure_dirs()
    assert upgrade(settings) is None
    current, head = current_and_head(settings.database_url)
    assert current == head
    assert upgrade(settings) is None  # niets te doen: geen back-up


def test_backup_voor_migratie_van_bestaande_database(settings):
    settings.ensure_dirs()
    db_file = settings.db_dir / "bridge.db"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE oud (x INTEGER)")
    con.execute("INSERT INTO oud VALUES (42)")
    con.commit()
    con.close()
    backup = upgrade(settings)
    assert backup is not None and backup.parent == settings.backup_dir
    copy = sqlite3.connect(backup)
    assert copy.execute("SELECT x FROM oud").fetchone() == (42,)
    copy.close()


def test_wal_modus(app, rt):
    with rt.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_migraties_gelijk_aan_modellen(settings):
    settings.ensure_dirs()
    upgrade(settings)
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
