"""`GET /api/v1/daily`: dagwaarden met dekking, kwaliteit en herkomst (sectie 8.4).

Het antwoord is een vast raster scope × metric × datum. Elke gevraagde dag komt terug, ook zonder
data (quality `ontbreekt`, value null). Daardoor is een offset-cursor stabiel, ook als er tussen
twee pagina's een import plaatsvindt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .catalog import Catalog, Metric
from .mapping import Structure
from .models import Observation

MAX_DAYS = 3700
MAX_SCOPES = 200


class BadRequest(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Scope:
    kind: str  # bedrijf | afdeling | kap | bron
    nummer: int | None = None
    bron_type: str | None = None

    @property
    def text(self) -> str:
        if self.kind == "bedrijf":
            return "bedrijf"
        if self.kind == "bron":
            return f"bron:{self.bron_type}:{self.nummer}"
        return f"{self.kind}:{self.nummer}"


def parse_scope(text: str, structure: Structure, catalog: Catalog) -> Scope:
    parts = text.strip().split(":")
    try:
        if parts == ["bedrijf"]:
            return Scope("bedrijf")
        if len(parts) == 2 and parts[0] == "afdeling":
            nummer = int(parts[1])
            if nummer in structure.afdeling_numbers():
                return Scope("afdeling", nummer)
        elif len(parts) == 2 and parts[0] == "kap":
            nummer = int(parts[1])
            if nummer in structure.kap_numbers():
                return Scope("kap", nummer)
        elif len(parts) == 3 and parts[0] == "bron":
            bron_types = set(catalog.bron_types.values()) - {"afdeling"}
            nummer = int(parts[2])
            if parts[1] in bron_types and nummer >= 0:
                return Scope("bron", nummer, parts[1])
    except ValueError:
        pass
    raise BadRequest(
        f"Ongeldige scope '{text}'. Gebruik bedrijf, afdeling:1–4, kap:1–40 of bron:<type>:<nummer>."
    )


def applicable(scope: Scope, metric: Metric, inherit: bool) -> str | None:
    """Hoe een metric op een scope wordt bepaald: direct, rollup, inherit of None (bestaat niet)."""
    if scope.kind == "bedrijf":
        return "direct" if "bedrijf" in metric.niveaus else None
    if scope.kind == "afdeling":
        return "direct" if "afdeling" in metric.niveaus else None
    if scope.kind == "kap":
        if "kap" in metric.niveaus and metric.kap_rollup and metric.bron_type:
            return "rollup"
        if "kap" in metric.niveaus and metric.bron_type is None:
            return "direct"
        if inherit and "afdeling" in metric.niveaus:
            return "inherit"
        return None
    return "direct" if metric.bron_type == scope.bron_type else None


def _fingerprint(scopes, start, end, metrics, inherit) -> str:
    raw = json.dumps([[s.text for s in scopes], start.isoformat(), end.isoformat(), metrics, inherit])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def encode_cursor(offset: int, fingerprint: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset, "h": fingerprint}).encode()).decode().rstrip("=")


def decode_cursor(cursor: str, fingerprint: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        offset = int(data["o"])
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise BadRequest("Ongeldige cursor.") from exc
    if data.get("h") != fingerprint or offset < 0:
        raise BadRequest("De cursor hoort bij andere parameters; begin opnieuw zonder cursor.")
    return offset


def query_daily(
    db: Session,
    catalog: Catalog,
    scope_texts: list[str],
    start: date,
    end: date,
    metric_keys: list[str] | None,
    inherit: bool,
    cursor: str | None,
    limit: int,
) -> tuple[list[dict], str | None]:
    if end < start:
        raise BadRequest("'to' ligt vóór 'from'.")
    days = (end - start).days + 1
    if days > MAX_DAYS:
        raise BadRequest(f"Periode te lang: maximaal {MAX_DAYS} dagen per verzoek.")
    structure = Structure(db)
    texts: list[str] = []
    for text in scope_texts:
        for part in text.split(","):
            if part.strip() and part.strip() not in texts:
                texts.append(part.strip())
    if not texts:
        raise BadRequest("Geef minstens één scope op.")
    if len(texts) > MAX_SCOPES:
        raise BadRequest(f"Maximaal {MAX_SCOPES} scopes per verzoek.")
    scopes = [parse_scope(t, structure, catalog) for t in texts]

    if metric_keys:
        keys: list[str] = []
        for text in metric_keys:
            keys.extend(k.strip() for k in text.split(",") if k.strip() and k.strip() not in keys)
        unknown = [k for k in keys if k not in catalog.metrics]
        if unknown:
            raise BadRequest(f"Onbekende metric: {', '.join(unknown)}. Zie /api/v1/metrics.")
        metrics = [catalog.metrics[k] for k in keys]
    else:
        keys = []
        metrics = list(catalog.metrics.values())

    pairs = [(s, m, how) for s in scopes for m in metrics if (how := applicable(s, m, inherit))]
    if not pairs:
        raise BadRequest(
            "Geen van de gevraagde meetwaarden bestaat op deze scope(s). Een kap erft afdelingswaarden "
            "alleen met inherit=true."
        )
    fingerprint = _fingerprint(scopes, start, end, keys, inherit)
    offset = decode_cursor(cursor, fingerprint) if cursor else 0
    total = len(pairs) * days
    page = range(offset, min(offset + limit, total))

    # Welke bronpunten zijn nodig voor deze pagina?
    expected_modules = catalog.expected_modules()
    devices_cache: dict[tuple, list[int]] = {}

    def kap_devices(kind: str, kap: int, day: date) -> list[int]:
        key = (kind, kap, day)
        if key not in devices_cache:
            found = structure.devices_for_kap(kind, kap, day)
            if kind == "module":
                found = [n for n in found if n in expected_modules]
            devices_cache[key] = found
        return devices_cache[key]

    plan = []
    points: set[str] = set()
    for index in page:
        scope, metric, how = pairs[index // days]
        day = start + timedelta(days=index % days)
        if how == "direct":
            sources = [scope.text] if metric.priva else []
            resolved = scope.text
        elif how == "inherit":
            afd = structure.afdeling_of_kap(scope.nummer, day)
            resolved = f"afdeling:{afd}" if afd else scope.text
            sources = [resolved] if afd else []
        else:
            resolved = scope.text
            sources = [
                f"bron:{metric.bron_type}:{n}" for n in kap_devices(metric.bron_type, scope.nummer, day)
            ]
        points.update(sources)
        plan.append((scope, metric, how, day, resolved, sources))

    latest: dict[tuple, Observation] = {}
    if plan and points:
        rows = db.scalars(
            select(Observation).where(
                Observation.metric_key.in_({p[1].key for p in plan}),
                Observation.point_key.in_(points),
                Observation.lokale_datum >= min(p[3] for p in plan),
                Observation.lokale_datum <= max(p[3] for p in plan),
            )
        )
        for obs in rows:
            ident = (obs.point_key, obs.metric_key, obs.lokale_datum)
            if ident not in latest or obs.revisie > latest[ident].revisie:
                latest[ident] = obs

    data = []
    for scope, metric, how, day, resolved, sources in plan:
        found = [latest.get((src, metric.key, day)) for src in sources]
        present = [o for o in found if o is not None and o.waarde is not None]
        used = [o for o in found if o is not None]
        if how == "rollup":
            value = sum(o.waarde for o in present) if present else None
            coverage = len(present) / len(sources) if sources else 0.0
        else:
            value = present[0].waarde if present else None
            coverage = 1.0 if present else 0.0
        if not present:
            quality = "ontbreekt"
        elif any(o.kwaliteit == "verdacht" for o in present):
            quality = "verdacht"
        elif coverage < 1:
            quality = "onvolledig"
        else:
            quality = "ok"
        newest = max(used, key=lambda o: (o.import_id, o.revisie), default=None)
        data.append(
            {
                "date": day.isoformat(),
                "scope": scope.text,
                "metric": metric.key,
                "value": value,
                "unit": metric.eenheid,
                "aggregation": metric.aggregatie,
                "coverage": round(coverage, 4),
                "quality": quality,
                "resolved_from": resolved,
                "sources": sources,
                "import_id": f"imp_{newest.import_id:04d}" if newest else None,
                "revision": max(o.revisie for o in used) if used else None,
            }
        )
    next_offset = offset + len(data)
    next_cursor = encode_cursor(next_offset, fingerprint) if next_offset < total else None
    return data, next_cursor
