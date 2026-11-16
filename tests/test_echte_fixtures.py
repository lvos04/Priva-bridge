"""Steekproefcontrole op de ECHTE Priva-exports van Leon.

De bestanden staan lokaal in tests/fixtures/echt/ (niet in git: bedrijfsgegevens, publieke repo).
Ontbreekt de map, dan slaan deze tests automatisch over (zoals in CI).

Optioneel: tests/fixtures/echt/steekproef.csv met kopregel `datum;scope;metric;waarde`, met waarden die
Leon rechtstreeks uit het Priva-rapport (scherm/PDF) overneemt. Die worden met /api/v1/daily vergeleken.
"""

import csv
from datetime import date
from pathlib import Path

import pytest

from bridge.parser_priva import parse_priva_date, read_rows, recognize_family
from tests.test_api import daily

ECHT = Path(__file__).parent / "fixtures" / "echt"
FILES = sorted(ECHT.glob("*.csv")) if ECHT.is_dir() else []
FILES = [f for f in FILES if f.name != "steekproef.csv"]

pytestmark = pytest.mark.skipif(not FILES, reason="Echte Priva-exports niet aanwezig in tests/fixtures/echt/")


def _family(path: Path) -> str:
    rows, _, _ = read_rows(path.read_bytes())
    labels = {r["label"] for _, r in rows}
    types = {r["type_1"] for _, r in rows}
    deps = {
        r["idx_1"] for _, r in rows if r["type_1"] == "Afdeling" and r["idx_1"] not in {"5", "6", "7", "8"}
    }
    return recognize_family(labels, types, deps)


def _by_family():
    return {_family(f): f for f in FILES}


def test_alle_vijf_families_op_inhoud_herkend():
    assert set(_by_family()) == {"klimaat", "belichting", "kranen", "algemeen_bedrijf", "algemeen_afdeling"}


def test_klimaat_en_belichting_importeren_zonder_handwerk_en_rest_stopt_netjes(importer, rt):
    for family, path in _by_family().items():
        batch, _ = importer(path.read_bytes(), path.name)
        errors = [m for m in batch.meldingen if m["niveau"] == "fout"]
        if family in ("klimaat", "belichting"):
            assert batch.status == "verwerkt", errors
            assert batch.aantallen["waarden"] > 0 and batch.aantallen["dagen"] == 7
        else:
            assert batch.status == "geweigerd" and errors[0]["code"] == "unsupported_family"


def test_driemaal_echte_bestanden_geen_dubbele_records(importer, rt):
    from sqlalchemy import func, select

    from bridge.models import Observation

    fams = _by_family()
    counts = []
    for _ in range(3):
        for family in ("klimaat", "belichting"):
            importer(fams[family].read_bytes(), fams[family].name)
        with rt.sessions() as db:
            counts.append(db.scalar(select(func.count(Observation.id))))
    assert counts[0] == counts[1] == counts[2] > 0


def _sample(path: Path, catalog, n: int = 5):
    """Deterministische steekproef uit het ruwe bestand, met een eigen, onafhankelijke parse."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [r for r in csv.DictReader(handle, delimiter=";") if r["value"].strip() and catalog.match(r)]
    step = max(1, len(rows) // n)
    return rows[::step][:n]


def test_tien_steekproefwaarden_daily_gelijk_aan_rapport(importer, client, auth, rt):
    fams = _by_family()
    for family in ("klimaat", "belichting"):
        importer(fams[family].read_bytes(), fams[family].name)
    checked = 0
    for family in ("klimaat", "belichting"):
        for row in _sample(fams[family], rt.catalog):
            metric = rt.catalog.match(row)
            bron = rt.catalog.bron_types[row["type_1"]]
            scope = f"afdeling:{row['idx_1']}" if bron == "afdeling" else f"bron:{bron}:{row['idx_1']}"
            day = parse_priva_date(row["startdate"]).isoformat()
            [result] = daily(client, auth, scope=scope, metric=metric.key, **{"from": day, "to": day})["data"]
            assert result["value"] == pytest.approx(float(row["value"].replace(",", "."))), (row, result)
            checked += 1
    assert checked == 10


STEEKPROEF = ECHT / "steekproef.csv"


@pytest.mark.skipif(
    not STEEKPROEF.exists(), reason="steekproef.csv van Leon (waarden uit het Priva-rapport) ontbreekt"
)
def test_steekproef_van_leon(importer, client, auth):
    for path in _by_family().values():
        importer(path.read_bytes(), path.name)
    with STEEKPROEF.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    assert len(rows) >= 10
    for row in rows:
        day = date.fromisoformat(row["datum"]).isoformat()
        [result] = daily(client, auth, scope=row["scope"], metric=row["metric"], **{"from": day, "to": day})[
            "data"
        ]
        assert result["value"] == pytest.approx(float(row["waarde"].replace(",", "."))), row
