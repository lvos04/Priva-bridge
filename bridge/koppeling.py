"""Bedrijfskoppeling API v1 (gedeeld contract, zie docs/koppeling-api.md): alleen-lezen KPI's en
datasets voor het Bedrijfsdashboard.

Koppelsleutels staan los van de `/api/v1`-tokens: eigen tabel, voorvoegsel `bk_`, alleen de SHA-256
opgeslagen. De schakelaar "Bedrijfskoppeling ingeschakeld" staat standaard uit; dan weigert elke
route met 403, ook met een geldige sleutel.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import __version__
from . import status as status_mod
from .models import ImportBatch, Instelling, Koppelsleutel, utcnow
from .runtime import Runtime, get_db, runtime
from .status import TZ, status

APP_SLUG = "priva-bridge"
APP_NAAM = "Priva Bridge"
PREFIX = "/api/koppeling/v1"
SLEUTEL_PREFIX = "bk_"
DATASETS = ("imports",)
INSTELLING = "koppeling_ingeschakeld"
GEBRUIK_BIJWERKEN_NA = timedelta(minutes=1)


class KoppelingFout(Exception):
    def __init__(self, status_code: int, fout: str, headers: dict | None = None):
        super().__init__(fout)
        self.status_code, self.fout, self.headers = status_code, fout, headers


async def _handler(_request: Request, exc: KoppelingFout) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"fout": exc.fout}, headers=exc.headers)


# --- instelling en sleutels --------------------------------------------------------------------
def is_ingeschakeld(db: Session) -> bool:
    row = db.get(Instelling, INSTELLING)
    return row is not None and row.waarde == "1"


def zet_ingeschakeld(db: Session, aan: bool) -> None:
    row = db.get(Instelling, INSTELLING) or Instelling(sleutel=INSTELLING)
    row.waarde = "1" if aan else "0"
    row.gewijzigd_op = utcnow()
    db.add(row)
    db.commit()


def sleutel_hash(sleutel: str) -> str:
    return hashlib.sha256(sleutel.encode()).hexdigest()


def maak_sleutel(db: Session, naam: str, door: str) -> tuple[Koppelsleutel, str]:
    naam = naam.strip()
    if not naam:
        raise ValueError("Geef de sleutel een naam")
    sleutel = SLEUTEL_PREFIX + secrets.token_urlsafe(32)
    row = Koppelsleutel(
        naam=naam[:100],
        voorvoegsel=sleutel[:8],
        sleutel_hash=sleutel_hash(sleutel),
        aangemaakt_door=door[:32],
    )
    db.add(row)
    db.commit()
    return row, sleutel


def trek_in(db: Session, sleutel_id: int) -> bool:
    row = db.get(Koppelsleutel, sleutel_id)
    if row is None:
        return False
    if row.ingetrokken_op is None:
        row.ingetrokken_op = utcnow()
        db.commit()
    return True


def vereis_koppelsleutel(
    request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(runtime)
) -> Koppelsleutel:
    if not is_ingeschakeld(db):
        raise KoppelingFout(403, "koppeling_uitgeschakeld")
    soort, _, sleutel = request.headers.get("authorization", "").partition(" ")
    sleutel = sleutel.strip()
    weiger = KoppelingFout(401, "ongeldige_koppelsleutel", {"WWW-Authenticate": "Bearer"})
    if soort.lower() != "bearer" or not sleutel.startswith(SLEUTEL_PREFIX) or len(sleutel) > 200:
        raise weiger
    digest = sleutel_hash(sleutel)
    row = db.scalar(select(Koppelsleutel).where(Koppelsleutel.sleutel_hash == digest))
    if row is None or not hmac.compare_digest(row.sleutel_hash, digest) or row.ingetrokken_op is not None:
        raise weiger
    retry = rt.koppeling_limiter.check(("koppeling", row.id))
    if retry is not None:
        raise KoppelingFout(429, "te_veel_verzoeken", {"Retry-After": str(retry)})
    now = utcnow()
    if row.laatst_gebruikt_op is None or now - row.laatst_gebruikt_op >= GEBRUIK_BIJWERKEN_NA:
        row.laatst_gebruikt_op = now
        db.commit()
    return row


router = APIRouter(prefix=PREFIX, tags=["bedrijfskoppeling"], dependencies=[Depends(vereis_koppelsleutel)])


def install(app: FastAPI) -> None:
    app.add_exception_handler(KoppelingFout, _handler)
    app.include_router(router)


def _tijd(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _nu() -> str:
    return _tijd(utcnow())


# --- endpoints ---------------------------------------------------------------------------------
@router.get("/info", summary="App-informatie")
def info():
    return {
        "app": APP_SLUG,
        "naam": APP_NAAM,
        "versie": __version__,
        "api_versie": "1",
        "bedrijf": None,
        "datasets": list(DATASETS),
        "tijd": _nu(),
    }


def _status_drempel(waarde: float | None, ok: float, let_op: float) -> str:
    if waarde is None:
        return "onbekend"
    if waarde <= ok:
        return "ok"
    return "let_op" if waarde <= let_op else "actie"


@router.get("/samenvatting", summary="Kern-KPI's en meldingen")
def samenvatting(db: Session = Depends(get_db), rt: Runtime = Depends(runtime)):
    st = status(db, rt.catalog)
    gisteren = status_mod.vandaag() - timedelta(days=1)
    laatste_import = st["last_import_at"]
    uren = None
    if laatste_import:
        moment = datetime.fromisoformat(laatste_import)
        uren = round((datetime.now(TZ) - moment).total_seconds() / 3600, 1)
    laatste_dag = st["last_complete_date"]
    achterstand = (gisteren - date.fromisoformat(laatste_dag)).days if laatste_dag else None
    venster = gisteren - timedelta(days=29)
    ontbrekend = [d for d in st["missing_dates"] if date.fromisoformat(d) >= venster]
    heeft_data = laatste_import is not None
    kpis = [
        {
            "sleutel": "laatste_import",
            "label": "Laatste import",
            "waarde": uren,
            "eenheid": "uur geleden",
            "status": _status_drempel(uren, 8 * 24, 14 * 24),
            "toelichting": f"Verwerkt op {laatste_import}" if laatste_import else "Nog geen import",
        },
        {
            "sleutel": "laatste_complete_dag",
            "label": "Achterstand laatste complete dag",
            "waarde": achterstand,
            "eenheid": "dagen",
            "status": _status_drempel(achterstand, 7, 14),
            "toelichting": f"Laatste complete dag {laatste_dag}" if laatste_dag else "Nog geen complete dag",
        },
        {
            "sleutel": "ontbrekende_dagen",
            "label": "Ontbrekende dagen (30 dagen)",
            "waarde": len(ontbrekend) if heeft_data else None,
            "eenheid": "dagen",
            "status": _status_drempel(len(ontbrekend) if heeft_data else None, 7, 14),
            "toelichting": "Dagen zonder alle verwachte afdelingen en lichtmodules",
        },
        {
            "sleutel": "waarschuwingen",
            "label": "Waarschuwingen",
            "waarde": len(st["warnings"]),
            "eenheid": None,
            "status": "ok" if not st["warnings"] else "let_op",
            "toelichting": ", ".join(sorted({w["code"] for w in st["warnings"]})) or None,
        },
    ]
    meldingen = [
        {
            "niveau": "waarschuwing",
            "tekst": w["detail"],
            "sinds": (
                datetime.combine(date.fromisoformat(w["since"]), datetime.min.time(), TZ).isoformat()
                if w["since"]
                else None
            ),
        }
        for w in st["warnings"]
    ]
    return {"app": APP_SLUG, "gegenereerd_op": _nu(), "kpis": kpis, "meldingen": meldingen}


def _cursor(last_id: int) -> str:
    return base64.urlsafe_b64encode(f"imports:{last_id}".encode()).decode().rstrip("=")


def _lees_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()).decode()
        naam, _, last = raw.partition(":")
        if naam != "imports":
            raise ValueError
        return int(last)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise KoppelingFout(422, "ongeldige_parameters") from exc


@router.get("/datasets/{naam}", summary="Dataset (alleen-lezen)")
def dataset(
    naam: str,
    sinds: datetime | None = Query(None),
    limiet: int = Query(100, ge=1, le=500),
    cursor: str | None = Query(None),
    db: Session = Depends(get_db),
):
    if naam not in DATASETS:
        raise KoppelingFout(404, "onbekende_dataset")
    query = select(ImportBatch).order_by(ImportBatch.id)
    if sinds is not None:
        if sinds.tzinfo is None:
            sinds = sinds.replace(tzinfo=UTC)
        grens = sinds.astimezone(UTC).replace(tzinfo=None)
        query = query.where(or_(ImportBatch.verwerkt_op >= grens, ImportBatch.ontvangen_op >= grens))
    if cursor:
        query = query.where(ImportBatch.id > _lees_cursor(cursor))
    rows = list(db.scalars(query.limit(limiet + 1)))
    meer = len(rows) > limiet
    rows = rows[:limiet]
    items = [
        {
            "id": b.import_id,
            "rapportfamilie": b.rapportfamilie,
            "status": b.status,
            "periode_van": b.periode_van.isoformat() if b.periode_van else None,
            "periode_tot": b.periode_tot.isoformat() if b.periode_tot else None,
            "aantal_waarden": (b.aantallen or {}).get("waarden"),
            "ontvangen_op": _tijd(b.ontvangen_op),
            "bijgewerkt_op": _tijd(max(filter(None, [b.ontvangen_op, b.verwerkt_op]))),
        }
        for b in rows
    ]
    return {
        "app": APP_SLUG,
        "dataset": naam,
        "gegenereerd_op": _nu(),
        "items": items,
        "volgende_cursor": _cursor(rows[-1].id) if meer and rows else None,
    }
