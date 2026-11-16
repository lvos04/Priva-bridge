"""Import: synthetische Klimaat en Belichting, leeg ≠ nul, dubbele uploads, revisies, stoppen bij onbekend."""

import hashlib
from datetime import date

from sqlalchemy import func, select

from bridge.ingest import raw_path
from bridge.models import ImportBatch, Observation, SourceDevice
from tests.conftest import BELICHTING, KLIMAAT, csv_bytes, klimaat_row, module_row

KLIMAAT_DAY = date(2026, 10, 19)


def _obs(rt, **where):
    with rt.sessions() as db:
        query = select(Observation)
        for key, value in where.items():
            query = query.where(getattr(Observation, key) == value)
        return list(db.scalars(query.order_by(Observation.revisie)))


def _count(rt):
    with rt.sessions() as db:
        return db.scalar(select(func.count(Observation.id)))


def test_beide_voorbeeldrapporten_importeren_zonder_handwerk(synthetic, rt):
    klimaat, belichting = synthetic
    assert klimaat.rapportfamilie == "klimaat" and belichting.rapportfamilie == "belichting"
    assert klimaat.aantallen["waarden"] == 119 and klimaat.aantallen["overgeslagen"] == 21
    assert belichting.aantallen["waarden"] == 2821
    assert belichting.aantallen["encoding"] == "cp1252"
    assert (klimaat.periode_van.isoformat(), klimaat.periode_tot.isoformat()) == ("2026-10-19", "2026-10-25")
    assert _count(rt) == 119 + 2821
    assert not [m for m in klimaat.meldingen + belichting.meldingen if m["niveau"] == "fout"]


def test_decimale_komma_wordt_getal(synthetic, rt):
    [obs] = _obs(rt, point_key="afdeling:1", metric_key="temp_etmaal_avg", lokale_datum=KLIMAAT_DAY)
    assert obs.waarde == 19.5 and obs.ruwe_waarde == "19,5"
    [energie] = _obs(rt, point_key="bron:module:101", metric_key="lamp_energie", lokale_datum=KLIMAAT_DAY)
    assert energie.waarde == 110.35


def test_leeg_veld_is_ontbreekt_en_echte_nul_blijft_nul(synthetic, rt):
    [leeg] = _obs(rt, point_key="afdeling:1", metric_key="temp_etmaal_avg", lokale_datum=date(2026, 10, 25))
    assert leeg.waarde is None and leeg.kwaliteit == "ontbreekt" and leeg.ruwe_waarde == ""
    [nul] = _obs(rt, point_key="afdeling:1", metric_key="scherm_seconden_doek2", lokale_datum=KLIMAAT_DAY)
    assert nul.waarde == 0.0 and nul.kwaliteit == "ok"
    [lamp_leeg] = _obs(
        rt, point_key="bron:module:105", metric_key="lamp_brandseconden", lokale_datum=date(2026, 10, 22)
    )
    assert lamp_leeg.waarde is None
    # Bekende lege kolommen en nepnullen (CompCO2, MeasuredPAR) worden niet opgeslagen, ook niet als 0.
    with rt.sessions() as db:
        metrics = set(db.scalars(select(Observation.metric_key).distinct()))
    assert not {m for m in metrics if "co2" in m and m != "co2_dosering"}


def test_driemaal_hetzelfde_bestand_geeft_geen_dubbele_records(importer, rt):
    data = KLIMAAT.read_bytes()
    first, _ = importer(data, KLIMAAT.name)
    count = _count(rt)
    for _ in range(2):
        again, result = importer(data, "andere-naam.csv")
        assert result.duplicate and again.id == first.id
    assert _count(rt) == count
    with rt.sessions() as db:
        assert db.scalar(select(func.count(ImportBatch.id))) == 1
    # Hetzelfde meetpunt in een ander bestand: herkend op meetpuntniveau, geen nieuwe records.
    subset = b"\n".join(data.splitlines()[:20]) + b"\n"
    other, _ = importer(subset, "deel.csv")
    assert other.id != first.id and other.aantallen["nieuw"] == 0
    assert other.aantallen["al_aanwezig"] == other.aantallen["waarden"] > 10
    assert _count(rt) == count


def test_afwijkende_herimport_geeft_revisie_en_leeg_wist_niets(importer, rt):
    importer(csv_bytes(klimaat_row(value="20,5")), "a.csv")
    importer(csv_bytes(klimaat_row(value="21")), "b.csv")
    importer(csv_bytes(klimaat_row(value="")), "c.csv")
    revisions = _obs(rt, point_key="afdeling:1", metric_key="temp_etmaal_avg")
    assert [(o.revisie, o.waarde) for o in revisions] == [(1, 20.5), (2, 21.0)]


def test_ruw_archief_bewaart_bestand_ongewijzigd(importer, rt):
    data = BELICHTING.read_bytes()
    batch, _ = importer(data, BELICHTING.name)
    path = raw_path(rt.settings, batch.sha256)
    assert path.read_bytes() == data
    assert hashlib.sha256(path.read_bytes()).hexdigest() == batch.sha256
    assert batch.parserversie and batch.ontvangen_op


def _refused(importer, rt, data):
    batch, _ = importer(data, "fout.csv")
    assert batch.status == "geweigerd"
    assert _count(rt) == 0
    return [m for m in batch.meldingen if m["niveau"] == "fout"]


def test_onbekende_kolom_stopt_import_met_melding(importer, rt):
    errors = _refused(importer, rt, csv_bytes(klimaat_row(), klimaat_row("Ave_24h_Nieuw", value="1")))
    assert errors[0]["code"] == "unknown_column"
    assert "Ave_24h_Nieuw" in errors[0]["detail"] and "niet in catalogus" in errors[0]["detail"]


def test_onbekende_rapportvariant_stopt_import(importer, rt):
    errors = _refused(importer, rt, csv_bytes(klimaat_row(), klimaat_row(type_1="Nieuw object")))
    assert errors[0]["code"] == "unknown_variant"
    errors = _refused(importer, rt, b"label;pcu;value\nA;B;1\n")
    assert errors[0]["code"] == "unknown_variant"


def test_bekende_lege_kolom_met_waarde_stopt_import(importer, rt):
    errors = _refused(
        importer, rt, csv_bytes(klimaat_row(), klimaat_row("Sum_24h_CalculatedPAR", value="12"))
    )
    assert errors[0]["code"] == "unexpected_value"


def test_ongeldige_waarde_datum_en_tegenstrijdige_dubbelen_stoppen(importer, rt):
    assert _refused(importer, rt, csv_bytes(klimaat_row(value="abc")))[0]["code"] == "invalid_value"
    assert _refused(importer, rt, csv_bytes(klimaat_row(day="31-2-26")))[0]["code"] == "invalid_date"
    assert _refused(importer, rt, csv_bytes(klimaat_row(value="1"), klimaat_row(value="2")))[0]["code"] == (
        "conflicting_duplicate"
    )


def test_rapportfamilie_van_latere_slice_stopt_netjes(importer, rt):
    errors = _refused(importer, rt, csv_bytes(klimaat_row("Sum_24h_ValveUse", 41, type_1="Kraan", value="5")))
    assert errors[0]["code"] == "unsupported_family" and "Kranen" in errors[0]["detail"]


def test_afdeling_5_tot_8_overgeslagen_en_9_geeft_melding(importer, rt):
    batch, _ = importer(csv_bytes(klimaat_row(), *[klimaat_row(afdeling=n) for n in (5, 6, 7, 8)]), "a.csv")
    assert batch.status == "verwerkt" and _count(rt) == 1
    assert batch.aantallen["overgeslagen_afdelingen"] == "5–8"
    assert batch.aantallen["overgeslagen_afdeling_regels"] == 4
    assert any("afdeling 5–8, genegeerd volgens mapping" in m["detail"] for m in batch.meldingen)
    batch, _ = importer(
        csv_bytes(klimaat_row("Ave_24h_DewPointDifference", value="3"), klimaat_row(afdeling=9)), "b.csv"
    )
    assert batch.status == "geweigerd"
    assert batch.meldingen[0]["code"] == "unknown_object" and "afdeling 9" in batch.meldingen[0]["detail"]


def test_onbekende_bronnen_worden_unmapped_opgeslagen(importer, rt):
    batch, _ = importer(csv_bytes(module_row(module=141), module_row(module=101)), "m.csv")
    assert batch.status == "verwerkt"
    assert any(m["code"] == "unmapped_source" and "bron:module:141" in m["detail"] for m in batch.meldingen)
    with rt.sessions() as db:
        assert db.scalar(
            select(SourceDevice).where(SourceDevice.type == "module", SourceDevice.extern_id == 141)
        )


def test_seconden_boven_daglengte_zijn_verdacht_behalve_op_25_uursdag(importer, rt):
    batch, _ = importer(
        csv_bytes(
            module_row(day="25-10-26", value="88000"),  # 25 uur: kan
            module_row(day="29-3-26", value="85000"),  # 23 uur: kan niet
            module_row(day="9-11-26", value="86400"),  # 24 uur: precies vol
        ),
        "dst.csv",
    )
    kwaliteit = {o.lokale_datum.isoformat(): o.kwaliteit for o in _obs(rt, point_key="bron:module:101")}
    assert kwaliteit == {"2026-10-25": "ok", "2026-03-29": "verdacht", "2026-11-09": "ok"}
    assert all(o.timestamp_utc is None for o in _obs(rt, point_key="bron:module:101"))
