"""API-tokens voor `/api/v1` (sectie 8.1): Bearer-token met scopes, HMAC-hash, rate limit."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ApiToken, utcnow
from .problems import Problem
from .runtime import Runtime, get_db, runtime

TOKEN_PREFIX = "pb_"
SCOPES = ("read:data", "admin:imports")
USAGE_RESOLUTION = timedelta(minutes=1)


def hash_token(secret_key: str, token: str) -> str:
    return hmac.new(secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def create_token(db: Session, secret_key: str, naam: str, scopes: list[str]) -> tuple[ApiToken, str]:
    invalid = [s for s in scopes if s not in SCOPES]
    if invalid or not scopes:
        raise ValueError(f"Ongeldige scope(s): {invalid or '(geen)'}; kies uit {', '.join(SCOPES)}")
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        naam=naam.strip()[:100], voorvoegsel=plain[:10], hash=hash_token(secret_key, plain), scopes=scopes
    )
    db.add(row)
    db.commit()
    return row, plain


def revoke_token(db: Session, token_id: int) -> bool:
    row = db.get(ApiToken, token_id)
    if row is None:
        return False
    if row.ingetrokken_op is None:
        row.ingetrokken_op = utcnow()
        db.commit()
    return True


def require_scope(scope: str):
    def dependency(
        request: Request, db: Session = Depends(get_db), rt: Runtime = Depends(runtime)
    ) -> ApiToken:
        header = request.headers.get("authorization", "")
        kind, _, token = header.partition(" ")
        token = token.strip()
        challenge = {"WWW-Authenticate": 'Bearer realm="priva-bridge"'}
        if kind.lower() != "bearer" or not token.startswith(TOKEN_PREFIX) or len(token) > 200:
            raise Problem(
                401,
                "niet-geauthenticeerd",
                "Geef een geldig token mee als 'Authorization: Bearer <token>'.",
                challenge,
            )
        digest = hash_token(rt.settings.secret_key, token)
        row = db.scalar(select(ApiToken).where(ApiToken.hash == digest))
        if row is None or not hmac.compare_digest(row.hash, digest) or row.ingetrokken_op is not None:
            raise Problem(401, "niet-geauthenticeerd", "Token onbekend of ingetrokken.", challenge)
        if scope not in (row.scopes or []):
            raise Problem(403, "scope-ontbreekt", f"Dit token mist de scope '{scope}'.")
        retry = rt.limiter.check(("api", row.id))
        if retry is not None:
            raise Problem(
                429, "te-veel-verzoeken", "Rate limit voor dit token bereikt.", {"Retry-After": str(retry)}
            )
        now = utcnow()
        if row.laatst_gebruikt is None or now - row.laatst_gebruikt >= USAGE_RESOLUTION:
            row.laatst_gebruikt = now
            db.commit()
        return row

    return dependency
