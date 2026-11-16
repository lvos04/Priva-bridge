"""App-fabriek: instellingen, migraties (met back-up), catalogus, routes."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, admin, api, koppeling
from .catalog import load_catalog, seed_mapping, sync_catalog
from .config import Settings, load_settings
from .db import make_engine, make_sessionmaker
from .migrate import upgrade
from .problems import Problem, http_handler, problem_handler, validation_handler
from .ratelimit import RateLimiter
from .runtime import Runtime

log = logging.getLogger("bridge")

DESCRIPTION = """Alleen-lezen API met Priva-dagwaarden (implementatieplan v1.1, sectie 8).

* Authenticatie: `Authorization: Bearer <token>`; scope `read:data` voor alle GET-endpoints.
* Fouten: `application/problem+json` met `type`, `title`, `status`, `detail`.
* Leeg is niet nul: een ontbrekende waarde is `null` met quality `ontbreekt`.
* Datums zijn lokale datums (Europe/Amsterdam).
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_dirs()
    upgrade(settings)
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    catalog = load_catalog(settings.catalog_dir)
    with sessions() as db:
        sync_catalog(db, catalog)
        seed_mapping(db, catalog)
        db.commit()

    app = FastAPI(
        title="Priva Bridge API",
        version=__version__,
        description=DESCRIPTION,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        redirect_slashes=False,
    )
    app.state.runtime = Runtime(
        settings=settings,
        engine=engine,
        sessions=sessions,
        catalog=catalog,
        limiter=RateLimiter(settings.rate_limit_per_minute),
        koppeling_limiter=RateLimiter(120),
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="bridge_sessie",
        same_site="strict",
        https_only=False,  # alleen intern netwerk, meestal zonder TLS (sectie 3.1)
        max_age=12 * 3600,
    )
    app.add_exception_handler(Problem, problem_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_handler)
    app.add_exception_handler(admin.NotLoggedIn, admin.not_logged_in_handler)
    app.include_router(api.health_router)
    app.include_router(api.router)
    koppeling.install(app)
    app.include_router(admin.router)

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        schema.setdefault("components", {}).setdefault("schemas", {})["Problem"] = api.PROBLEM_SCHEMA
        schema["components"]["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
        for path, ops in schema["paths"].items():
            if path.startswith("/api/"):
                for op in ops.values():
                    op["security"] = [{"bearer": []}]
        # De app-versie hoort niet in het vastgelegde contract; het contract heeft zijn eigen versie.
        schema["info"]["version"] = "v1"
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
    return app
