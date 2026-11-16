"""Parser: formaat, getallen, datums, zomer-/wintertijd en rapportfamilie op inhoud."""

from datetime import date

import pytest

from bridge.parser_priva import (
    ImportRefused,
    day_seconds,
    parse_priva_date,
    parse_priva_datetime,
    parse_value,
    read_rows,
    recognize_family,
)
from tests.conftest import BELICHTING, HEADER, KLIMAAT, csv_bytes, klimaat_row


def test_decimale_komma_en_punt():
    assert parse_value("20,5") == 20.5
    assert parse_value("3,35") == 3.35
    assert parse_value("20.5") == 20.5
    assert parse_value("-1,25") == -1.25
    assert parse_value("19500") == 19500.0


def test_leeg_veld_is_none_en_nul_is_nul():
    assert parse_value("") is None
    assert parse_value("   ") is None
    assert parse_value("0") == 0.0
    assert parse_value("0,0") == 0.0


@pytest.mark.parametrize("raw", ["abc", "1.234,5", "1,2,3", "nan", "inf", "1e5"])
def test_ongeldige_getallen(raw):
    with pytest.raises(ValueError):
        parse_value(raw)


def test_priva_datums():
    assert parse_priva_date("9-11-26") == date(2026, 11, 9)
    assert parse_priva_date("10-11-25") == date(2025, 11, 10)
    assert parse_priva_date("01-02-2024") == date(2024, 2, 1)
    assert parse_priva_date("1/2/26") == date(2026, 2, 1)
    for bad in ("1-1-99", "31-2-26", "2026-11-09", "9-11-26 02:30"):
        with pytest.raises(ValueError):
            parse_priva_date(bad)


def test_dagen_van_23_en_25_uur():
    assert day_seconds(date(2026, 3, 29)) == 23 * 3600
    assert day_seconds(date(2026, 10, 25)) == 25 * 3600
    assert day_seconds(date(2026, 11, 9)) == 24 * 3600


def test_tijdstippen_rond_wissel_worden_geweigerd():
    with pytest.raises(ValueError):
        parse_priva_datetime("29-3-26 02:30")  # bestaat niet
    with pytest.raises(ValueError):
        parse_priva_datetime("25-10-26 02:30")  # bestaat twee keer
    assert parse_priva_datetime("1-7-26 12:00").utcoffset().total_seconds() == 7200
    assert parse_priva_datetime("1-12-26 12:00").utcoffset().total_seconds() == 3600


def test_utf8_bom_cp1252_en_tab():
    rows, enc, delim = read_rows(b"\xef\xbb\xbf" + csv_bytes(klimaat_row()))
    assert (enc, delim, rows[0][1]["value"]) == ("utf-8", ";", "20,5")
    rows, enc, _ = read_rows(BELICHTING.read_bytes())
    assert enc == "cp1252" and rows[0][1]["pcu"] == "SYNTHETISCH – testdata"
    tab = csv_bytes(klimaat_row().replace(";", "\t"), header=HEADER.replace(";", "\t"))
    rows, _, delim = read_rows(tab)
    assert delim == "\t" and rows[0][1]["label"] == "Ave_24h_CompTemp"


def test_onbekende_kopregel_of_veldaantal_stopt():
    with pytest.raises(ImportRefused) as exc:
        read_rows(b"label;pcu;value\nA;B;1\n")
    assert exc.value.code == "unknown_variant"
    with pytest.raises(ImportRefused) as exc:
        read_rows(csv_bytes("a;b;c"))
    assert exc.value.code == "unknown_variant"
    with pytest.raises(ImportRefused) as exc:
        read_rows(b"")
    assert exc.value.code == "empty_file"


def _family(rows):
    parsed, _, _ = read_rows(csv_bytes(*rows))
    labels = {r["label"] for _, r in parsed}
    types = {r["type_1"] for _, r in parsed}
    deps = {r["idx_1"] for _, r in parsed if r["type_1"] == "Afdeling"}
    return recognize_family(labels, types, deps)


def test_familieherkenning_op_inhoud():
    # Zelfde gevallen als de importer van de gewichtsapp (gebouwd op de echte bestanden).
    cases = {
        "kranen": [klimaat_row("Sum_24h_ValveUse", 41, type_1="Kraan", value="5")],
        "belichting": ["Ave_24h_ModGrowlightPAR;S;modgroeilichtsturing;101;kanaal;2;10-11-25;10-11-25;0"],
        "algemeen_afdeling": [klimaat_row("Sum_24h_VgUse", 1, type_1="Kraangroep", value="1")],
        "algemeen_bedrijf": [klimaat_row(afdeling=1), klimaat_row(afdeling=2)],
        "klimaat": [klimaat_row()],
    }
    for expected, rows in cases.items():
        assert _family(rows) == expected
    # Klimaat met kenmerkende labels blijft Klimaat, ook met meerdere afdelingen.
    assert _family([klimaat_row("Ave_24h_DewPointDifference", 1), klimaat_row(afdeling=2)]) == "klimaat"
    assert _family([klimaat_row("Sum_24h_CO2ProgActive", 1)]) == "algemeen_bedrijf"
    with pytest.raises(ImportRefused):
        _family([klimaat_row("Iets_Onbekends")])


def test_bestandsnaam_doet_er_niet_toe(importer):
    batch, _ = importer(KLIMAAT.read_bytes(), "Rapport_Belichting_misleidend.csv", commit=False)
    assert batch.rapportfamilie == "klimaat"
