"""Opstartcommando `bridge`: server starten, migreren, tokens en koppelsleutels beheren.

Op TrueNAS: Apps → priva-bridge → Shell, dan bijvoorbeeld
    bridge token aanmaken --naam gewichtsapp --scope read:data
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select


def _session():
    from .catalog import load_catalog, seed_mapping, sync_catalog
    from .config import load_settings
    from .db import make_engine, make_sessionmaker
    from .migrate import upgrade

    settings = load_settings()
    settings.ensure_dirs()
    upgrade(settings)
    sessions = make_sessionmaker(make_engine(settings.database_url))
    db = sessions()
    catalog = load_catalog(settings.catalog_dir)
    sync_catalog(db, catalog)
    seed_mapping(db, catalog)
    db.commit()
    return settings, db


def cmd_serve(args) -> int:
    import uvicorn

    from .main import create_app

    uvicorn.run(
        create_app(), host=args.host, port=args.port, workers=1, proxy_headers=False, log_level="info"
    )
    return 0


def cmd_migrate(_args) -> int:
    from .config import load_settings
    from .migrate import upgrade

    settings = load_settings()
    settings.ensure_dirs()
    backup = upgrade(settings)
    print(f"Database bijgewerkt.{' Back-up: ' + str(backup) if backup else ''}")
    return 0


def cmd_token(args) -> int:
    from .auth import SCOPES, create_token, revoke_token
    from .models import ApiToken

    settings, db = _session()
    with db:
        if args.actie == "aanmaken":
            scopes = args.scope or ["read:data"]
            try:
                row, plain = create_token(db, settings.secret_key, args.naam, scopes)
            except ValueError as exc:
                print(exc, file=sys.stderr)
                return 2
            print(f"Token {row.id} '{row.naam}' met scopes {', '.join(row.scopes)}.")
            print("Bewaar dit token nu; het wordt nergens anders getoond:")
            print(plain)
        elif args.actie == "lijst":
            for row in db.scalars(select(ApiToken).order_by(ApiToken.id)):
                state = "ingetrokken" if row.ingetrokken_op else "actief"
                print(f"{row.id}\t{row.naam}\t{row.voorvoegsel}…\t{','.join(row.scopes)}\t{state}")
        elif args.actie == "intrekken":
            if not revoke_token(db, args.id):
                print("Token niet gevonden", file=sys.stderr)
                return 1
            print(f"Token {args.id} ingetrokken.")
        else:
            print(f"Scopes: {', '.join(SCOPES)}")
    return 0


def cmd_koppeling(args) -> int:
    from .koppeling import is_ingeschakeld, maak_sleutel, trek_in, zet_ingeschakeld
    from .models import Koppelsleutel

    _settings, db = _session()
    with db:
        if args.actie == "aanmaken":
            row, sleutel = maak_sleutel(db, args.naam, door="cli")
            print(f"Koppelsleutel {row.id} '{row.naam}'. Alleen nu zichtbaar:")
            print(sleutel)
        elif args.actie == "lijst":
            print(f"Bedrijfskoppeling ingeschakeld: {'ja' if is_ingeschakeld(db) else 'nee'}")
            for row in db.scalars(select(Koppelsleutel).order_by(Koppelsleutel.id)):
                print(
                    f"{row.id}\t{row.naam}\t{row.voorvoegsel}…\t"
                    f"{'ingetrokken' if row.ingetrokken_op else 'actief'}"
                )
        elif args.actie == "intrekken":
            if not trek_in(db, args.id):
                print("Sleutel niet gevonden", file=sys.stderr)
                return 1
            print(f"Koppelsleutel {args.id} ingetrokken.")
        elif args.actie in ("aan", "uit"):
            zet_ingeschakeld(db, args.actie == "aan")
            print(f"Bedrijfskoppeling {'ingeschakeld' if args.actie == 'aan' else 'uitgeschakeld'}.")
    return 0


def openapi_document() -> dict:
    import os
    import tempfile

    from .config import REPO_ROOT, Settings
    from .main import create_app

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            data_dir=Path(tmp),
            database_url=f"sqlite:///{tmp}/openapi.db",
            catalog_dir=Path(os.environ.get("BRIDGE_CATALOG_DIR", REPO_ROOT / "catalog")),
            admin_password=None,
            secret_key="openapi",
        )
        app = create_app(settings)
        document = app.openapi()
        app.state.runtime.engine.dispose()
        return document


def render_openapi(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def cmd_openapi(args) -> int:
    text = render_openapi(openapi_document())
    if args.schrijf:
        Path(args.schrijf).write_text(text, encoding="utf-8")
        print(f"Geschreven: {args.schrijf}")
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bridge", description="Priva Bridge")
    sub = parser.add_subparsers(dest="commando", required=True)
    serve = sub.add_parser("serve", help="Migreren en de webserver starten")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)
    sub.add_parser("migrate", help="Database migreren (met back-up)").set_defaults(func=cmd_migrate)

    token = sub.add_parser("token", help="API-tokens voor /api/v1")
    tsub = token.add_subparsers(dest="actie", required=True)
    t_new = tsub.add_parser("aanmaken")
    t_new.add_argument("--naam", required=True)
    t_new.add_argument("--scope", action="append", help="read:data (standaard) en/of admin:imports")
    tsub.add_parser("lijst")
    t_rev = tsub.add_parser("intrekken")
    t_rev.add_argument("id", type=int)
    token.set_defaults(func=cmd_token)

    kop = sub.add_parser("koppeling", help="Bedrijfskoppeling: schakelaar en koppelsleutels")
    ksub = kop.add_subparsers(dest="actie", required=True)
    k_new = ksub.add_parser("aanmaken")
    k_new.add_argument("--naam", required=True)
    ksub.add_parser("lijst")
    k_rev = ksub.add_parser("intrekken")
    k_rev.add_argument("id", type=int)
    ksub.add_parser("aan")
    ksub.add_parser("uit")
    kop.set_defaults(func=cmd_koppeling)

    oa = sub.add_parser("openapi", help="OpenAPI-specificatie uitvoeren")
    oa.add_argument("--schrijf", metavar="PAD")
    oa.set_defaults(func=cmd_openapi)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
