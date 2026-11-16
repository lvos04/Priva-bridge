"""Bedrijfskoppeling API v1 (alleen-lezen) voor het Bedrijfsdashboard."""

import hashlib

from bridge.koppeling import maak_sleutel, trek_in, zet_ingeschakeld
from bridge.models import Koppelsleutel

PREFIX = "/api/koppeling/v1"


def _key(rt, aan=True):
    with rt.sessions() as db:
        _row, sleutel = maak_sleutel(db, "Bedrijfsdashboard", door="test")
        zet_ingeschakeld(db, aan)
    return sleutel


def test_standaard_uitgeschakeld_403(client, rt):
    sleutel = _key(rt, aan=False)
    response = client.get(f"{PREFIX}/info", headers={"Authorization": f"Bearer {sleutel}"})
    assert response.status_code == 403 and response.json() == {"fout": "koppeling_uitgeschakeld"}


def test_401_zonder_ongeldige_of_ingetrokken_sleutel(client, rt):
    sleutel = _key(rt)
    for headers in ({}, {"Authorization": "Bearer bk_onzin"}, {"Authorization": "Bearer pb_xyz"}):
        response = client.get(f"{PREFIX}/info", headers=headers)
        assert response.status_code == 401 and response.json() == {"fout": "ongeldige_koppelsleutel"}
    with rt.sessions() as db:
        row = db.query(Koppelsleutel).one()
        assert row.sleutel_hash == hashlib.sha256(sleutel.encode()).hexdigest()
        assert row.voorvoegsel == sleutel[:8] and sleutel.startswith("bk_") and len(sleutel) > 40
        trek_in(db, row.id)
    assert client.get(f"{PREFIX}/info", headers={"Authorization": f"Bearer {sleutel}"}).status_code == 401


def test_api_v1_token_werkt_niet_op_koppeling_en_omgekeerd(client, rt, make_token):
    _key(rt)
    assert (
        client.get(f"{PREFIX}/info", headers={"Authorization": f"Bearer {make_token()}"}).status_code == 401
    )
    sleutel = _key(rt)
    assert client.get("/api/v1/status", headers={"Authorization": f"Bearer {sleutel}"}).status_code == 401


def test_info_en_samenvatting(client, rt, synthetic):
    headers = {"Authorization": f"Bearer {_key(rt)}"}
    info = client.get(f"{PREFIX}/info", headers=headers).json()
    assert info["app"] == "priva-bridge" and info["api_versie"] == "1" and info["versie"] == "0.1.0"
    assert info["datasets"] == ["imports"] and info["tijd"].endswith("Z")
    body = client.get(f"{PREFIX}/samenvatting", headers=headers).json()
    assert body["app"] == "priva-bridge" and body["gegenereerd_op"].endswith("Z")
    kpis = {k["sleutel"]: k for k in body["kpis"]}
    assert set(kpis) == {"laatste_import", "laatste_complete_dag", "ontbrekende_dagen", "waarschuwingen"}
    assert kpis["laatste_complete_dag"]["waarde"] == 0 and kpis["laatste_complete_dag"]["status"] == "ok"
    assert kpis["ontbrekende_dagen"]["waarde"] == 0
    assert kpis["waarschuwingen"]["waarde"] == 1  # unmapped bronnen
    for kpi in body["kpis"]:
        assert kpi["status"] in {"ok", "let_op", "actie", "onbekend"}
    for melding in body["meldingen"]:
        assert melding["niveau"] in {"info", "waarschuwing", "kritiek"}


def test_samenvatting_zonder_data_is_onbekend_niet_nul(client, rt):
    headers = {"Authorization": f"Bearer {_key(rt)}"}
    kpis = {k["sleutel"]: k for k in client.get(f"{PREFIX}/samenvatting", headers=headers).json()["kpis"]}
    assert kpis["laatste_import"]["waarde"] is None and kpis["laatste_import"]["status"] == "onbekend"
    assert kpis["ontbrekende_dagen"]["waarde"] is None


def test_dataset_imports_limiet_sinds_cursor(client, rt, synthetic):
    headers = {"Authorization": f"Bearer {_key(rt)}"}
    body = client.get(f"{PREFIX}/datasets/imports", params={"limiet": 1}, headers=headers).json()
    assert body["dataset"] == "imports" and len(body["items"]) == 1 and body["volgende_cursor"]
    item = body["items"][0]
    assert item["id"] == "imp_0001" and item["status"] == "verwerkt" and "bestandsnaam" not in item
    rest = client.get(
        f"{PREFIX}/datasets/imports", params={"cursor": body["volgende_cursor"]}, headers=headers
    ).json()
    assert [i["id"] for i in rest["items"]] == ["imp_0002"] and rest["volgende_cursor"] is None
    later = client.get(
        f"{PREFIX}/datasets/imports", params={"sinds": "2999-01-01T00:00:00Z"}, headers=headers
    ).json()
    assert later["items"] == []
    assert (
        client.get(f"{PREFIX}/datasets/imports", params={"limiet": 501}, headers=headers).status_code == 422
    )
    assert (
        client.get(f"{PREFIX}/datasets/imports", params={"sinds": "gisteren"}, headers=headers).status_code
        == 422
    )
    response = client.get(f"{PREFIX}/datasets/onbekend", headers=headers)
    assert response.status_code == 404 and response.json() == {"fout": "onbekende_dataset"}


def test_niet_beheerders_kunnen_geen_sleutels_beheren(client, rt):
    response = client.post(
        "/beheer/koppeling/sleutels", data={"naam": "x", "csrf": "x"}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/beheer/login"
    response = client.post("/beheer/koppeling/schakelaar", data={"aan": "1"}, follow_redirects=False)
    assert response.status_code == 303
    with rt.sessions() as db:
        assert db.query(Koppelsleutel).count() == 0
