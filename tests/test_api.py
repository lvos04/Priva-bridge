"""API-contract sectie 8: auth, problem+json, /health, /status, /metrics, /daily."""

from datetime import date

import pytest

from bridge.auth import revoke_token
from bridge.models import ApiToken

PROBLEM_KEYS = {"type", "title", "status", "detail"}


def assert_problem(response, status):
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert set(body) == PROBLEM_KEYS and body["status"] == status
    return body


def test_health_zonder_token(client):
    assert client.get("/health").json() == {"status": "ok", "version": "0.1.0"}


def test_401_zonder_ongeldig_of_ingetrokken_token(client, rt, make_token):
    for headers in ({}, {"Authorization": "Bearer pb_onzin"}, {"Authorization": "Basic abc"}):
        response = client.get("/api/v1/status", headers=headers)
        assert_problem(response, 401)
        assert response.headers["www-authenticate"].startswith("Bearer")
    token = make_token()
    with rt.sessions() as db:
        row = db.query(ApiToken).one()
        assert row.hash != token and token not in row.hash  # alleen hash opgeslagen
        revoke_token(db, row.id)
    assert_problem(client.get("/api/v1/status", headers={"Authorization": f"Bearer {token}"}), 401)


def test_403_zonder_scope(client, make_token):
    token = make_token(scopes=("admin:imports",))
    body = assert_problem(client.get("/api/v1/metrics", headers={"Authorization": f"Bearer {token}"}), 403)
    assert "read:data" in body["detail"]


def test_429_met_retry_after(client, rt, make_token):
    from bridge.ratelimit import RateLimiter

    rt.limiter = RateLimiter(3)
    headers = {"Authorization": f"Bearer {make_token()}"}
    for _ in range(3):
        assert client.get("/api/v1/metrics", headers=headers).status_code == 200
    response = client.get("/api/v1/metrics", headers=headers)
    assert_problem(response, 429)
    assert int(response.headers["retry-after"]) >= 1
    # Een ander token heeft een eigen limiet.
    assert (
        client.get("/api/v1/metrics", headers={"Authorization": f"Bearer {make_token()}"}).status_code == 200
    )


def test_404_400_422_als_problem_json(client, auth):
    assert_problem(client.get("/api/v1/bestaat-niet", headers=auth), 404)
    assert_problem(
        client.get(
            "/api/v1/daily",
            params={"scope": "kap:99", "from": "2026-10-19", "to": "2026-10-19"},
            headers=auth,
        ),
        400,
    )
    assert_problem(
        client.get(
            "/api/v1/daily", params={"scope": "kap:1", "from": "2026-10-20", "to": "2026-10-19"}, headers=auth
        ),
        400,
    )
    assert_problem(
        client.get(
            "/api/v1/daily",
            params={
                "scope": "afdeling:1",
                "from": "2026-10-19",
                "to": "2026-10-19",
                "metric": "bestaat_niet",
            },
            headers=auth,
        ),
        400,
    )
    assert_problem(
        client.get("/api/v1/daily", params={"scope": "kap:1", "from": "x", "to": "2026-10-19"}, headers=auth),
        422,
    )
    assert_problem(
        client.get(
            "/api/v1/daily",
            params={"scope": "kap:1", "from": "2026-10-19", "to": "2026-10-19", "limit": 5001},
            headers=auth,
        ),
        422,
    )
    assert_problem(
        client.get("/api/v1/daily", params={"from": "2026-10-19", "to": "2026-10-19"}, headers=auth), 422
    )


def test_metrics_catalogus(client, auth):
    data = {m["metric_key"]: m for m in client.get("/api/v1/metrics", headers=auth).json()["data"]}
    assert data["temp_etmaal_avg"]["unit"] == "°C" and data["temp_etmaal_avg"]["level"] == "afdeling"
    assert data["lamp_brandseconden"]["aggregation"] == "sum" and data["lamp_brandseconden"]["levels"] == [
        "module",
        "kap",
    ]
    assert (
        data["vochtdeficit_avg"]["unit"] == "onbevestigd"
        and data["vochtdeficit_avg"]["expected_unit"] == "g/m³"
    )
    assert "Ave_24h_CompTemp" in data["temp_etmaal_avg"]["priva_names"]


def daily(client, auth, **params):
    response = client.get("/api/v1/daily", params=params, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def test_daily_velden_en_waarden(client, auth, synthetic):
    body = daily(
        client,
        auth,
        scope="afdeling:1",
        **{"from": "2026-10-19", "to": "2026-10-19"},
        metric="temp_etmaal_avg",
    )
    assert body["next_cursor"] is None
    assert body["freshness"]["last_complete_date"] == "2026-10-25"
    assert body["freshness"]["last_import_at"].endswith(("+01:00", "+02:00"))
    [row] = body["data"]
    assert row == {
        "date": "2026-10-19",
        "scope": "afdeling:1",
        "metric": "temp_etmaal_avg",
        "value": 19.5,
        "unit": "°C",
        "aggregation": "mean",
        "coverage": 1.0,
        "quality": "ok",
        "resolved_from": "afdeling:1",
        "sources": ["afdeling:1"],
        "import_id": "imp_0001",
        "revision": 1,
    }


def test_daily_ontbrekende_dagen_en_afdelingen_als_ontbreekt(client, auth, synthetic):
    body = daily(
        client,
        auth,
        scope=["afdeling:1", "afdeling:2"],
        metric="temp_etmaal_avg",
        **{"from": "2026-10-24", "to": "2026-10-27"},
    )
    rows = {(r["scope"], r["date"]): r for r in body["data"]}
    assert len(rows) == 8  # elke gevraagde dag, ook zonder data
    assert rows[("afdeling:1", "2026-10-24")]["quality"] == "ok"
    leeg = rows[("afdeling:1", "2026-10-25")]  # leeg veld in het bestand
    assert (leeg["value"], leeg["coverage"], leeg["quality"], leeg["import_id"]) == (
        None,
        0.0,
        "ontbreekt",
        "imp_0001",
    )
    geen = rows[("afdeling:1", "2026-10-26")]  # dag na de export
    assert (geen["value"], geen["quality"], geen["import_id"], geen["revision"]) == (
        None,
        "ontbreekt",
        None,
        None,
    )
    assert all(
        r["quality"] == "ontbreekt" and r["value"] is None for (s, _), r in rows.items() if s == "afdeling:2"
    )


def test_daily_kap_optelling_bron_en_echte_nul(client, auth, synthetic):
    body = daily(
        client,
        auth,
        scope=["kap:12", "bron:module:112", "bron:module:106", "kap:5"],
        metric=["lamp_brandseconden", "lamp_brandseconden_kanaal2"],
        **{"from": "2026-10-22", "to": "2026-10-22"},
    )
    rows = {(r["scope"], r["metric"]): r for r in body["data"]}
    kap = rows[("kap:12", "lamp_brandseconden")]
    assert kap["value"] == 36720 + 30 and kap["sources"] == ["bron:module:112"] and kap["coverage"] == 1.0
    assert rows[("bron:module:112", "lamp_brandseconden")]["value"] == 36750
    nul = rows[("bron:module:106", "lamp_brandseconden_kanaal2")]
    assert (nul["value"], nul["quality"]) == (0.0, "ok")
    leeg = rows[("kap:5", "lamp_brandseconden")]  # module 105 kanaal 1 leeg op 22-10
    assert (leeg["value"], leeg["quality"], leeg["coverage"]) == (None, "ontbreekt", 0.0)


def test_daily_25_uursdag(client, auth, synthetic):
    body = daily(
        client,
        auth,
        scope="bron:module:112",
        metric="lamp_brandseconden",
        **{"from": "2026-10-24", "to": "2026-10-26"},
    )
    assert [(r["date"], r["value"], r["quality"]) for r in body["data"]] == [
        ("2026-10-24", 36770.0, "ok"),
        ("2026-10-25", 88312.0, "ok"),
        ("2026-10-26", None, "ontbreekt"),
    ]


def test_daily_inherit(client, auth, synthetic):
    params = {"scope": "kap:5", "metric": "temp_etmaal_avg", "from": "2026-10-19", "to": "2026-10-19"}
    assert client.get("/api/v1/daily", params=params, headers=auth).status_code == 400  # zonder inherit
    [row] = daily(client, auth, inherit="true", **params)["data"]
    assert (row["value"], row["resolved_from"], row["scope"]) == (19.5, "afdeling:1", "kap:5")
    [row] = daily(client, auth, inherit="true", **{**params, "scope": "kap:12"})["data"]
    assert (row["value"], row["resolved_from"], row["quality"]) == (None, "afdeling:2", "ontbreekt")


@pytest.mark.parametrize("limit", [1, 7, 50])
def test_daily_cursor(client, auth, synthetic, limit):
    params = {
        "scope": ["afdeling:1", "kap:1"],
        "metric": ["temp_etmaal_avg", "lamp_brandseconden", "rv_avg"],
        "from": "2026-10-18",
        "to": "2026-10-26",
    }
    full = daily(client, auth, **params, limit=5000)["data"]
    assert len(full) == (2 + 1) * 9  # afdeling: 2 metrics; kap zonder inherit: alleen lamp_brandseconden
    pages, cursor = [], None
    while True:
        body = daily(client, auth, **params, limit=limit, **({"cursor": cursor} if cursor else {}))
        pages.extend(body["data"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert isinstance(cursor, str)
    assert pages == full


def test_daily_cursor_bij_andere_parameters_geweigerd(client, auth, synthetic):
    body = daily(client, auth, scope="afdeling:1", **{"from": "2026-10-19", "to": "2026-10-25"}, limit=2)
    response = client.get(
        "/api/v1/daily",
        params={
            "scope": "afdeling:2",
            "from": "2026-10-19",
            "to": "2026-10-25",
            "cursor": body["next_cursor"],
        },
        headers=auth,
    )
    assert_problem(response, 400)
    assert_problem(
        client.get(
            "/api/v1/daily",
            params={"scope": "afdeling:2", "from": "2026-10-19", "to": "2026-10-25", "cursor": "!!!"},
            headers=auth,
        ),
        400,
    )


def test_status(client, auth, synthetic, today):
    body = client.get("/api/v1/status", headers=auth).json()
    assert body["last_complete_date"] == "2026-10-25" and body["missing_dates"] == []
    assert body["source_mode"] == "manual"
    assert {w["code"] for w in body["warnings"]} == {"unmapped_source"}
    today["value"] = date(2026, 10, 30)
    body = client.get("/api/v1/status", headers=auth).json()
    assert body["missing_dates"] == ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"]
    assert any(w["code"] == "achterstand" for w in body["warnings"])


def test_status_toont_geweigerde_import(client, auth, importer):
    from tests.conftest import csv_bytes, klimaat_row

    importer(csv_bytes(klimaat_row(), klimaat_row("Ave_24h_X", value="1")), "x.csv")
    warning = next(
        w
        for w in client.get("/api/v1/status", headers=auth).json()["warnings"]
        if w["code"] == "unknown_column"
    )
    assert "Klimaat: kolom 'Ave_24h_X'" in warning["detail"] and warning["since"]


def test_onvolledige_dag_zonder_belichting(client, auth, importer, today):
    from tests.conftest import KLIMAAT

    importer(KLIMAAT.read_bytes(), KLIMAAT.name)
    body = client.get("/api/v1/status", headers=auth).json()
    assert body["last_complete_date"] is None and len(body["missing_dates"]) == 7
