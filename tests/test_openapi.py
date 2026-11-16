"""De vastgelegde OpenAPI-specificatie (openapi/openapi.json) moet gelijk zijn aan wat de app levert.

Bijwerken na een bewuste, niet-brekende wijziging:  bridge openapi --schrijf openapi/openapi.json
"""

import json

from bridge.cli import openapi_document, render_openapi
from bridge.config import REPO_ROOT

COMMITTED = REPO_ROOT / "openapi" / "openapi.json"


def test_openapi_ongewijzigd():
    generated = render_openapi(openapi_document())
    assert COMMITTED.read_text(encoding="utf-8") == generated, (
        "OpenAPI wijkt af van openapi/openapi.json. Brekende wijziging? Dan /api/v2. "
        "Anders: bridge openapi --schrijf openapi/openapi.json"
    )


def test_openapi_bevat_contract(client):
    spec = client.get("/api/v1/openapi.json").json()
    for path in ("/health", "/api/v1/status", "/api/v1/metrics", "/api/v1/daily"):
        assert path in spec["paths"]
    params = {p["name"] for p in spec["paths"]["/api/v1/daily"]["get"]["parameters"]}
    assert params == {"scope", "from", "to", "metric", "inherit", "cursor", "limit"}
    assert spec["components"]["schemas"]["Problem"]["required"] == ["type", "title", "status", "detail"]
    assert not any(p.startswith("/beheer") for p in spec["paths"])
    assert json.loads(COMMITTED.read_text())["info"]["version"] == "v1"
