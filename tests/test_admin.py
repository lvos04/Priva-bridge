"""Beheer-UI: login, CSRF, upload met preview, sleutel eenmalig tonen."""

import re

from bridge.models import Koppelsleutel, Observation
from tests.conftest import KLIMAAT


def csrf(client, path="/beheer/login"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def login(client, wachtwoord="geheim-wachtwoord"):
    return client.post(
        "/beheer/login", data={"wachtwoord": wachtwoord, "csrf": csrf(client)}, follow_redirects=False
    )


def test_beheer_vraagt_login(client):
    response = client.get("/beheer", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/beheer/login"


def test_fout_wachtwoord_en_csrf(client):
    assert "Onjuist wachtwoord" in login(client, "fout").text
    response = client.post("/beheer/login", data={"wachtwoord": "geheim-wachtwoord", "csrf": "nep"})
    assert response.status_code == 403


def test_upload_preview_en_verwerken(client, rt):
    assert login(client).status_code == 303
    token = csrf(client, "/beheer/upload")
    response = client.post(
        "/beheer/upload",
        data={"csrf": token},
        files={"bestand": (KLIMAAT.name, KLIMAAT.read_bytes(), "text/csv")},
    )
    assert response.status_code == 200 and "Preview" in response.text and "Klimaat" in response.text
    assert "2026-10-19 t/m 2026-10-25" in response.text
    with rt.sessions() as db:
        assert db.query(Observation).count() == 0  # preview schrijft niets
    batch_id = re.search(r"/beheer/imports/(\d+)/verwerken", response.text).group(1)
    response = client.post(f"/beheer/imports/{batch_id}/verwerken", data={"csrf": token})
    assert "Verwerkt op" in response.text
    with rt.sessions() as db:
        assert db.query(Observation).count() == 119
    # Opnieuw uploaden: herkend als al verwerkt.
    response = client.post(
        "/beheer/upload",
        data={"csrf": token},
        files={"bestand": ("kopie.csv", KLIMAAT.read_bytes(), "text/csv")},
    )
    assert "al eerder verwerkt" in response.text
    assert "compleet" in client.get("/beheer").text or "onvolledig" in client.get("/beheer").text


def test_upload_onbekende_kolom_toont_melding(client):
    login(client)
    token = csrf(client, "/beheer/upload")
    data = KLIMAAT.read_bytes() + b"Ave_24h_Onbekend;S;Afdeling;1;;;19-10-26;19-10-26;1\n"
    response = client.post(
        "/beheer/upload", data={"csrf": token}, files={"bestand": ("x.csv", data, "text/csv")}
    )
    assert (
        "Niet verwerkt" in response.text
        and "Ave_24h_Onbekend" in response.text
        and "Verwerken</button>" not in response.text
    )


def test_koppelsleutel_eenmalig_tonen(client, rt):
    login(client)
    token = csrf(client, "/beheer/koppeling")
    response = client.post("/beheer/koppeling/sleutels", data={"csrf": token, "naam": "Bedrijfsdashboard"})
    sleutel = re.search(r"<code>(bk_[^<]+)</code>", response.text).group(1)
    assert response.headers["cache-control"] == "no-store"
    assert sleutel not in client.get("/beheer/koppeling").text
    with rt.sessions() as db:
        assert db.query(Koppelsleutel).one().sleutel_hash != sleutel
    client.post("/beheer/koppeling/schakelaar", data={"csrf": token, "aan": "1"})
    assert (
        client.get("/api/koppeling/v1/info", headers={"Authorization": f"Bearer {sleutel}"}).status_code
        == 200
    )
