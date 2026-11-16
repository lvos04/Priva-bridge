"""Consumenten-API `/api/v1` (sectie 8). Dit is een contract: brekende wijzigingen vragen /api/v2."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import __version__
from .auth import require_scope
from .daily import BadRequest, query_daily
from .problems import Problem, problem_response
from .runtime import Runtime, get_db, runtime
from .status import freshness, status

PROBLEM_RESPONSES = {
    code: {
        "description": desc,
        "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}},
    }
    for code, desc in {
        400: "Ongeldig verzoek",
        401: "Geen of ongeldig token",
        403: "Token mist de vereiste scope",
        422: "Ongeldige parameters",
        429: "Rate limit bereikt (zie Retry-After)",
    }.items()
}


PROBLEM_SCHEMA = {
    "title": "Problem",
    "type": "object",
    "required": ["type", "title", "status", "detail"],
    "properties": {
        "type": {"type": "string"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string"},
    },
}


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class Freshness(BaseModel):
    last_import_at: str | None = Field(description="Laatste verwerkte import, lokale tijd met offset")
    last_complete_date: str | None = Field(description="Laatste dag met alle verwachte bronnen (YYYY-MM-DD)")


class Warning(BaseModel):
    code: str
    detail: str
    since: str | None


class Status(Freshness):
    missing_dates: list[str]
    warnings: list[Warning]
    source_mode: Literal["manual", "smb"]


class MetricInfo(BaseModel):
    metric_key: str
    description: str
    unit: str = Field(description="Eenheid in /daily; 'onbevestigd' = waarde ongewijzigd doorgegeven (O7)")
    expected_unit: str | None
    level: str = Field(description="Hoofdniveau: bedrijf, afdeling, kap of bron:<type>")
    levels: list[str] = Field(
        description="Alle niveaus: bedrijf, afdeling, kap of een brontype (module, doek, ...)"
    )
    aggregation: str
    report: str | None
    status: str
    priva_names: list[str]


class Metrics(BaseModel):
    data: list[MetricInfo]


class DailyRow(BaseModel):
    date: str
    scope: str
    metric: str
    value: float | None = Field(description="null bij quality 'ontbreekt', nooit 0")
    unit: str
    aggregation: str
    coverage: float = Field(ge=0, le=1)
    quality: Literal["ok", "verdacht", "onvolledig", "ontbreekt"]
    resolved_from: str
    sources: list[str]
    import_id: str | None
    revision: int | None


class Daily(BaseModel):
    data: list[DailyRow]
    next_cursor: str | None
    freshness: Freshness


health_router = APIRouter()
router = APIRouter(prefix="/api/v1", responses=PROBLEM_RESPONSES)


@health_router.get("/health", response_model=Health, tags=["status"], summary="Leeft de app")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - elke databasefout betekent: niet gezond
        return problem_response(503, "database", "Database niet bereikbaar.")
    return {"status": "ok", "version": __version__}


@router.get(
    "/status",
    response_model=Status,
    tags=["status"],
    summary="Laatste import, laatste complete dag, achterstand, waarschuwingen",
    dependencies=[Depends(require_scope("read:data"))],
)
def get_status(db: Session = Depends(get_db), rt: Runtime = Depends(runtime)):
    return status(db, rt.catalog)


@router.get(
    "/metrics",
    response_model=Metrics,
    tags=["catalogus"],
    summary="Catalogus met eenheid, niveau en aggregatie",
    dependencies=[Depends(require_scope("read:data"))],
)
def get_metrics(rt: Runtime = Depends(runtime)):
    return {
        "data": [
            {
                "metric_key": m.key,
                "description": m.omschrijving,
                "unit": m.eenheid,
                "expected_unit": m.verwachte_eenheid,
                "level": m.hoofdniveau,
                "levels": m.niveaus,
                "aggregation": m.aggregatie,
                "report": m.rapport,
                "status": m.status,
                "priva_names": sorted({p.label for p in m.priva}),
            }
            for m in rt.catalog.metrics.values()
        ]
    }


@router.get(
    "/daily",
    response_model=Daily,
    tags=["data"],
    summary="Dagwaarden met dekking en kwaliteit",
    description=(
        "Een gevraagde dag zonder data komt terug als regel met quality `ontbreekt` en value null. "
        "Volgorde: scope (zoals gevraagd), metric (catalogusvolgorde of zoals gevraagd), datum."
    ),
    dependencies=[Depends(require_scope("read:data"))],
)
def get_daily(
    scope: list[str] = Query(..., description="Een of meer: bedrijf, afdeling:N, kap:N, bron:<type>:N"),
    from_: date = Query(..., alias="from", description="Lokale datum (Europe/Amsterdam), inclusief"),
    to: date = Query(..., description="Lokale datum (Europe/Amsterdam), inclusief"),
    metric: list[str] | None = Query(None, description="Optioneel, een of meer metric-sleutels"),
    inherit: bool = Query(False, description="Kap krijgt afdelingswaarden; zie resolved_from"),
    cursor: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
    rt: Runtime = Depends(runtime),
):
    try:
        data, next_cursor = query_daily(db, rt.catalog, scope, from_, to, metric, inherit, cursor, limit)
    except BadRequest as exc:
        raise Problem(400, "ongeldig-verzoek", exc.detail) from exc
    return JSONResponse({"data": data, "next_cursor": next_cursor, "freshness": freshness(db, rt.catalog)})
