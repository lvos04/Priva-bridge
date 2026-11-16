"""Server-side beheer-UI (sectie 9, slice 1): login, statusoverzicht met kalender, upload met preview,
en de schakelaar en sleutels van de Bedrijfskoppeling.

Technische foutdetails zijn alleen hier zichtbaar, nooit in de consumenten-API.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections import deque
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import __version__
from . import status as status_mod
from .ingest import ManualSource, commit_batch
from .koppeling import is_ingeschakeld, maak_sleutel, trek_in, zet_ingeschakeld
from .models import ApiToken, ImportBatch, Koppelsleutel
from .runtime import Runtime, get_db, runtime
from .status import day_states, local_iso, status

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["versie"] = __version__
templates.env.filters["lokaal"] = lambda v: (local_iso(v) or "–").replace("T", " ")[:16]
router = APIRouter(include_in_schema=False)

_failures: dict[str, deque] = {}
MAX_FAILURES = 5
FAILURE_WINDOW = 300


class NotLoggedIn(Exception):
    pass


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def require_admin(request: Request) -> None:
    if request.session.get("admin") is not True:
        raise NotLoggedIn()


def check_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(expected, token or ""):
        raise HTTPException(403, "CSRF-controle mislukt; laad de pagina opnieuw.")


def render(request: Request, name: str, **context) -> HTMLResponse:
    context.setdefault("ingelogd", request.session.get("admin") is True)
    return templates.TemplateResponse(request, name, {"csrf": csrf_token(request), **context})


@router.get("/")
def root():
    return RedirectResponse("/beheer", status_code=303)


@router.get("/beheer/login")
def login_form(request: Request, rt: Runtime = Depends(runtime)):
    return render(request, "login.html", uitgeschakeld=rt.settings.admin_password is None, fout=None)


@router.post("/beheer/login")
def login(request: Request, wachtwoord: str = Form(""), csrf: str = Form(""), rt: Runtime = Depends(runtime)):
    check_csrf(request, csrf)
    expected = rt.settings.admin_password
    client = request.client.host if request.client else "?"
    now = time.monotonic()
    hits = _failures.setdefault(client, deque())
    while hits and now - hits[0] > FAILURE_WINDOW:
        hits.popleft()
    if len(hits) >= MAX_FAILURES:
        return render(
            request,
            "login.html",
            uitgeschakeld=False,
            fout="Te veel pogingen; probeer het over enkele minuten opnieuw.",
        )
    if expected is None or not hmac.compare_digest(wachtwoord.encode(), expected.encode()):
        hits.append(now)
        return render(request, "login.html", uitgeschakeld=expected is None, fout="Onjuist wachtwoord.")
    hits.clear()
    request.session.clear()
    request.session["admin"] = True
    request.session["csrf"] = secrets.token_urlsafe(32)
    return RedirectResponse("/beheer", status_code=303)


@router.post("/beheer/logout")
def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/beheer/login", status_code=303)


@router.get("/beheer", dependencies=[Depends(require_admin)])
def overzicht(request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(runtime)):
    eind = status_mod.vandaag() - timedelta(days=1)
    begin = eind - timedelta(days=29)
    kalender = day_states(db, rt.catalog, begin, eind)
    imports = list(db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc()).limit(15)))
    tokens = list(db.scalars(select(ApiToken).order_by(ApiToken.id)))
    return render(
        request,
        "status.html",
        st=status(db, rt.catalog),
        kalender=[(d, s) for d, s in kalender.items() if begin <= d <= eind],
        imports=imports,
        tokens=tokens,
        verwacht_afdelingen=rt.catalog.expected_departments(),
        verwacht_modules=len(rt.catalog.expected_modules()),
    )


@router.get("/beheer/upload", dependencies=[Depends(require_admin)])
def upload_form(request: Request):
    return render(request, "upload.html", fout=None)


@router.post("/beheer/upload", dependencies=[Depends(require_admin)])
async def upload(
    request: Request,
    bestand: UploadFile = File(...),
    csrf: str = Form(""),
    db: Session = Depends(get_db),
    rt: Runtime = Depends(runtime),
):
    check_csrf(request, csrf)
    data = await bestand.read(rt.settings.max_upload_bytes + 1)
    if len(data) > rt.settings.max_upload_bytes:
        return render(request, "upload.html", fout="Bestand te groot.")
    if data[:2] == b"PK":
        return render(
            request, "upload.html", fout="ZIP-bestanden volgen in slice 3; upload de CSV-bestanden los."
        )
    result = ManualSource(db, rt.settings, rt.catalog).submit(bestand.filename or "upload.csv", data)
    return RedirectResponse(
        f"/beheer/imports/{result.batch.id}?dubbel={int(result.duplicate)}", status_code=303
    )


@router.get("/beheer/imports/{batch_id}", dependencies=[Depends(require_admin)])
def import_detail(
    request: Request,
    batch_id: int,
    dubbel: int = 0,
    db: Session = Depends(get_db),
    rt: Runtime = Depends(runtime),
):
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "Import niet gevonden")
    return render(
        request,
        "import.html",
        batch=batch,
        dubbel=bool(dubbel),
        familie=rt.catalog.family_name(batch.rapportfamilie or ""),
    )


@router.post("/beheer/imports/{batch_id}/verwerken", dependencies=[Depends(require_admin)])
def import_verwerken(
    request: Request,
    batch_id: int,
    csrf: str = Form(""),
    db: Session = Depends(get_db),
    rt: Runtime = Depends(runtime),
):
    check_csrf(request, csrf)
    try:
        commit_batch(db, rt.settings, rt.catalog, batch_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/beheer/imports/{batch_id}", status_code=303)


@router.get("/beheer/koppeling", dependencies=[Depends(require_admin)])
def koppeling_pagina(request: Request, db: Session = Depends(get_db), nieuwe_sleutel: str | None = None):
    sleutels = list(db.scalars(select(Koppelsleutel).order_by(Koppelsleutel.id)))
    return render(
        request,
        "koppeling.html",
        ingeschakeld=is_ingeschakeld(db),
        sleutels=sleutels,
        nieuwe_sleutel=nieuwe_sleutel,
    )


@router.post("/beheer/koppeling/schakelaar", dependencies=[Depends(require_admin)])
def koppeling_schakelaar(
    request: Request, csrf: str = Form(""), aan: str = Form("0"), db: Session = Depends(get_db)
):
    check_csrf(request, csrf)
    zet_ingeschakeld(db, aan == "1")
    return RedirectResponse("/beheer/koppeling", status_code=303)


@router.post("/beheer/koppeling/sleutels", dependencies=[Depends(require_admin)])
def koppeling_sleutel_maken(
    request: Request, csrf: str = Form(""), naam: str = Form(""), db: Session = Depends(get_db)
):
    check_csrf(request, csrf)
    try:
        _row, sleutel = maak_sleutel(db, naam, door="beheerder")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # De sleutel wordt eenmalig getoond en nergens opgeslagen.
    response = koppeling_pagina(request, db, nieuwe_sleutel=sleutel)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/beheer/koppeling/sleutels/{sleutel_id}/intrekken", dependencies=[Depends(require_admin)])
def koppeling_sleutel_intrekken(
    request: Request, sleutel_id: int, csrf: str = Form(""), db: Session = Depends(get_db)
):
    check_csrf(request, csrf)
    if not trek_in(db, sleutel_id):
        raise HTTPException(404, "Sleutel niet gevonden")
    return RedirectResponse("/beheer/koppeling", status_code=303)


async def not_logged_in_handler(request: Request, _exc: NotLoggedIn) -> Response:
    return RedirectResponse("/beheer/login", status_code=303)
