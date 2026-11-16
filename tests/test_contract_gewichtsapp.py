"""Contract met de eerste afnemer (gewichtsapp), gebouwd tegen sectie 8. Breekt deze test, dan breekt
de afnemer: wijzig dan niet de test maar maak /api/v2."""

from tests.test_api import daily


def test_1_metrics_vorm(client, auth):
    body = client.get("/api/v1/metrics", headers=auth).json()
    assert set(body) == {"data"}
    for item in body["data"]:
        assert {"metric_key", "unit", "level", "aggregation"} <= set(item)


def test_3_4_metric_keys_en_eenheden(client, auth):
    units = {m["metric_key"]: m["unit"] for m in client.get("/api/v1/metrics", headers=auth).json()["data"]}
    assert units["lichtsom_lampen_kap"] == "mol/m²/dag"
    assert units["temp_etmaal_avg"] == "°C"
    assert units["rv_avg"] == "%"
    assert units["lamp_brandseconden"] == "s"
    scherm = [k for k in units if k.startswith("scherm_seconden")]
    assert scherm and all(units[k] == "s" for k in scherm)


def test_4_lichtsom_bestaat_maar_ontbreekt_in_slice_1(client, auth, synthetic):
    [row] = daily(
        client,
        auth,
        scope="kap:12",
        metric="lichtsom_lampen_kap",
        **{"from": "2026-10-19", "to": "2026-10-19"},
    )["data"]
    assert (row["value"], row["quality"], row["coverage"], row["unit"]) == (
        None,
        "ontbreekt",
        0.0,
        "mol/m²/dag",
    )


def test_2_5_6_herhaalde_parameters_cursor_freshness_types(client, auth, synthetic):
    body = daily(
        client,
        auth,
        scope=["kap:1", "kap:2"],
        metric=["lamp_brandseconden", "temp_etmaal_avg"],
        inherit="true",
        **{"from": "2026-10-19", "to": "2026-10-21"},
        limit=5,
    )
    assert isinstance(body["next_cursor"], str)
    assert set(body["freshness"]) == {"last_import_at", "last_complete_date"}
    rows = list(body["data"])
    while body["next_cursor"]:
        body = daily(
            client,
            auth,
            scope=["kap:1", "kap:2"],
            metric=["lamp_brandseconden", "temp_etmaal_avg"],
            inherit="true",
            **{"from": "2026-10-19", "to": "2026-10-21"},
            limit=5,
            cursor=body["next_cursor"],
        )
        assert "freshness" in body
        rows.extend(body["data"])
    assert body["next_cursor"] is None
    assert {(r["scope"], r["metric"]) for r in rows} == {
        (s, m) for s in ("kap:1", "kap:2") for m in ("lamp_brandseconden", "temp_etmaal_avg")
    }
    for row in rows:
        assert isinstance(row["revision"], int) and 0 <= row["coverage"] <= 1


def test_7_inherit_resolved_from(client, auth, synthetic):
    [row] = daily(
        client,
        auth,
        scope="kap:9",
        metric="temp_etmaal_avg",
        inherit="true",
        **{"from": "2026-10-20", "to": "2026-10-20"},
    )["data"]
    assert row["resolved_from"] == "afdeling:1" and row["value"] == 19.75


def test_8_kappen_1_40_afdelingen_1_4(client, auth, synthetic):
    ok = {"from": "2026-10-19", "to": "2026-10-19", "metric": "lamp_brandseconden"}
    assert (
        client.get(
            "/api/v1/daily", params={**ok, "scope": [f"kap:{n}" for n in range(1, 41)]}, headers=auth
        ).status_code
        == 200
    )
    for bad in ("kap:0", "kap:41", "afdeling:5", "afdeling:0"):
        assert client.get("/api/v1/daily", params={**ok, "scope": bad}, headers=auth).status_code == 400


def test_9_geen_redirects(client, auth):
    for path in ("/api/v1/status/", "/api/v1/daily/", "/api/v1/metrics/"):
        response = client.get(path, headers=auth, follow_redirects=False)
        assert response.status_code == 404 and not response.is_redirect
    assert not client.get("/api/v1/status", follow_redirects=False).is_redirect  # 401, geen login-redirect
