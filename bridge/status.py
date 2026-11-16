"""Actualiteit: laatste import, complete dagen, ontbrekende dagen en waarschuwingen."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .catalog import Catalog
from .config import TZ
from .models import ImportBatch, Mapping, Observation, SourceDevice


def vandaag() -> date:
    return datetime.now(TZ).date()


def local_iso(value: datetime | None) -> str | None:
    """Opgeslagen tijden zijn naïeve UTC; uitgegeven met Europe/Amsterdam-offset."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC).astimezone(TZ).isoformat(timespec="seconds")


def expected_points(catalog: Catalog) -> dict[str, set[str]]:
    return {
        "klimaat": {f"afdeling:{n}" for n in catalog.expected_departments()},
        "belichting": {f"bron:module:{n}" for n in catalog.expected_modules()},
    }


def day_states(db: Session, catalog: Catalog, start: date | None = None, end: date | None = None) -> dict:
    """Per dag `compleet`, `onvolledig` of `ontbreekt` (alleen dagen in [start, end] of met data)."""
    expected = expected_points(catalog)
    all_points = set().union(*expected.values())
    query = select(Observation.lokale_datum, Observation.point_key).where(
        Observation.waarde.is_not(None), Observation.point_key.in_(all_points)
    )
    if start:
        query = query.where(Observation.lokale_datum >= start)
    if end:
        query = query.where(Observation.lokale_datum <= end)
    present: dict[date, set[str]] = {}
    for day, point in db.execute(query.distinct()):
        present.setdefault(day, set()).add(point)
    states = {}
    days = set(present)
    if start and end:
        days |= {start + timedelta(days=i) for i in range((end - start).days + 1)}
    for day in sorted(days):
        found = present.get(day, set())
        if all(points <= found for points in expected.values() if points):
            states[day] = "compleet"
        elif found:
            states[day] = "onvolledig"
        else:
            states[day] = "ontbreekt"
    return states


def freshness(db: Session, catalog: Catalog) -> dict:
    last_import = db.scalar(select(func.max(ImportBatch.verwerkt_op)).where(ImportBatch.status == "verwerkt"))
    states = day_states(db, catalog)
    complete = [d for d, s in states.items() if s == "compleet"]
    return {
        "last_import_at": local_iso(last_import),
        "last_complete_date": max(complete).isoformat() if complete else None,
    }


def status(db: Session, catalog: Catalog) -> dict:
    fresh = freshness(db, catalog)
    yesterday = vandaag() - timedelta(days=1)
    first = db.scalar(select(func.min(Observation.lokale_datum)))
    missing: list[str] = []
    if first is not None and first <= yesterday:
        states = day_states(db, catalog, first, yesterday)
        missing = [d.isoformat() for d, s in states.items() if s != "compleet" and first <= d <= yesterday]
    warnings: list[dict] = []
    if first is None:
        warnings.append(
            {"code": "geen_data", "detail": "Er zijn nog geen dagwaarden geïmporteerd.", "since": None}
        )

    latest_ok: dict[str | None, datetime] = {}
    for fam, moment in db.execute(
        select(ImportBatch.rapportfamilie, func.max(ImportBatch.ontvangen_op))
        .where(ImportBatch.status == "verwerkt")
        .group_by(ImportBatch.rapportfamilie)
    ):
        latest_ok[fam] = moment
    for batch in db.scalars(
        select(ImportBatch).where(ImportBatch.status == "geweigerd").order_by(ImportBatch.ontvangen_op)
    ):
        if batch.rapportfamilie in latest_ok and latest_ok[batch.rapportfamilie] > batch.ontvangen_op:
            continue
        for melding in (m for m in batch.meldingen or [] if m.get("niveau") == "fout"):
            warnings.append(
                {
                    "code": melding["code"],
                    "detail": f"{batch.bestandsnaam}: {melding['detail']}",
                    "since": batch.ontvangen_op.replace(tzinfo=UTC).astimezone(TZ).date().isoformat(),
                }
            )

    unmapped = db.scalar(
        select(func.count(func.distinct(SourceDevice.id)))
        .join(Mapping, Mapping.source_device_id == SourceDevice.id)
        .join(Observation, Observation.source_device_id == SourceDevice.id)
        .where(Mapping.status == "unmapped")
    )
    if unmapped:
        warnings.append(
            {
                "code": "unmapped_source",
                "detail": f"{unmapped} bronnen met data hebben nog geen kap of afdeling (unmapped).",
                "since": None,
            }
        )
    if first is not None:
        last_complete = fresh["last_complete_date"]
        if last_complete is None or date.fromisoformat(last_complete) < yesterday:
            behind = (yesterday - date.fromisoformat(last_complete)).days if last_complete else None
            detail = (
                f"Laatste complete dag {last_complete}; {behind} dag(en) achterstand."
                if last_complete
                else "Nog geen enkele complete dag (alle verwachte afdelingen en modules)."
            )
            warnings.append({"code": "achterstand", "detail": detail, "since": None})
    return {
        **fresh,
        "missing_dates": missing,
        "warnings": warnings,
        "source_mode": "manual",
    }
