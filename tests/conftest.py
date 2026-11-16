"""Gedeelde testopzet. Standaard alleen synthetische fixtures (tests/fixtures/synthetic)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridge import status as status_module
from bridge.auth import create_token
from bridge.config import REPO_ROOT, Settings
from bridge.ingest import ManualSource, commit_batch
from bridge.main import create_app

SYNTH = Path(__file__).parent / "fixtures" / "synthetic"
KLIMAAT = SYNTH / "SYNTHETISCH_klimaat_2026-10-19_2026-10-25.csv"
BELICHTING = SYNTH / "SYNTHETISCH_belichting_2026-10-19_2026-10-25_cp1252.csv"
HEADER = "label;pcu;type_1;idx_1;type_2;idx_2;startdate;enddate;value"


def csv_bytes(*rows: str, header: str = HEADER, encoding: str = "utf-8") -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode(encoding)


def klimaat_row(
    label="Ave_24h_CompTemp", afdeling=1, day="19-10-26", value="20,5", type_1="Afdeling", type_2="", idx_2=""
) -> str:
    return f"{label};SYNTHETISCH;{type_1};{afdeling};{type_2};{idx_2};{day};{day};{value}"


def module_row(
    label="Sum_24h_ModGrowlightSysHours", module=101, kanaal=1, day="19-10-26", value="36000"
) -> str:
    return f"{label};SYNTHETISCH;modgroeilichtsturing;{module};kanaal;{kanaal};{day};{day};{value}"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'data' / 'db' / 'bridge.db'}",
        catalog_dir=REPO_ROOT / "catalog",
        admin_password="geheim-wachtwoord",
        secret_key="test-secret",
        rate_limit_per_minute=1000,
    )


@pytest.fixture
def today(monkeypatch):
    """Vaste 'vandaag' zodat status en achterstand reproduceerbaar zijn."""
    current = {"value": date(2026, 10, 26)}
    monkeypatch.setattr(status_module, "vandaag", lambda: current["value"])
    return current


@pytest.fixture
def app(settings, today):
    application = create_app(settings)
    yield application
    application.state.runtime.engine.dispose()


@pytest.fixture
def rt(app):
    return app.state.runtime


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_token(rt):
    def _make(scopes=("read:data",), naam="test"):
        with rt.sessions() as db:
            _row, plain = create_token(db, rt.settings.secret_key, naam, list(scopes))
        return plain

    return _make


@pytest.fixture
def auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
def importer(rt):
    """Bestand ontvangen én verwerken (zoals upload + 'Verwerken' in de UI)."""

    def _import(data: bytes, name: str = "upload.csv", commit: bool = True):
        with rt.sessions() as db:
            result = ManualSource(db, rt.settings, rt.catalog).submit(name, data)
            batch = result.batch
            if commit and batch.status == "preview":
                batch = commit_batch(db, rt.settings, rt.catalog, batch.id)
            return batch, result

    return _import


@pytest.fixture
def synthetic(importer):
    k, _ = importer(KLIMAAT.read_bytes(), KLIMAAT.name)
    b, _ = importer(BELICHTING.read_bytes(), BELICHTING.name)
    assert k.status == "verwerkt", k.meldingen
    assert b.status == "verwerkt", b.meldingen
    return k, b
