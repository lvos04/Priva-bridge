"""Fouten als `application/problem+json` (RFC 9457): {"type", "title", "status", "detail"}."""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_TYPE = "urn:priva-bridge:probleem:"
TITLES = {
    400: "Ongeldig verzoek",
    401: "Niet geauthenticeerd",
    403: "Geen toegang",
    404: "Niet gevonden",
    405: "Methode niet toegestaan",
    422: "Ongeldige parameters",
    429: "Te veel verzoeken",
    503: "Niet beschikbaar",
}


class Problem(Exception):
    def __init__(self, status: int, code: str, detail: str, headers: dict | None = None):
        super().__init__(detail)
        self.status, self.code, self.detail, self.headers = status, code, detail, headers or {}


def problem_response(status: int, code: str, detail: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": PROBLEM_TYPE + code,
            "title": TITLES.get(status, "Fout"),
            "status": status,
            "detail": detail,
        },
        headers=headers,
    )


def _is_koppeling(request: Request) -> bool:
    return request.url.path.startswith("/api/koppeling/")


async def problem_handler(_request: Request, exc: Problem) -> JSONResponse:
    return problem_response(exc.status, exc.code, exc.detail, exc.headers)


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []) if p not in ("query", "path", "body"))
        parts.append(f"{loc}: {err.get('msg')}")
    detail = "; ".join(parts) or "Ongeldige parameters."
    if _is_koppeling(request):
        return JSONResponse(status_code=422, content={"fout": "ongeldige_parameters", "detail": detail})
    return problem_response(422, "ongeldige-parameters", detail)


async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if _is_koppeling(request):
        fout = "niet_gevonden" if exc.status_code == 404 else "fout"
        return JSONResponse(status_code=exc.status_code, content={"fout": fout}, headers=exc.headers)
    if request.url.path.startswith("/api/") or request.url.path == "/health":
        code = {404: "niet-gevonden", 405: "methode-niet-toegestaan"}.get(exc.status_code, "fout")
        detail = exc.detail if isinstance(exc.detail, str) else TITLES.get(exc.status_code, "Fout")
        if exc.status_code == 404:
            detail = "Dit endpoint of object bestaat niet."
        return problem_response(exc.status_code, code, detail, exc.headers)
    from fastapi.exception_handlers import http_exception_handler

    return await http_exception_handler(request, exc)
