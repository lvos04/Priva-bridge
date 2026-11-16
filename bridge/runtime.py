"""Gedeelde toestand van één app-instantie (instellingen, database, catalogus, limiter)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .catalog import Catalog
from .config import Settings
from .ratelimit import RateLimiter


@dataclass
class Runtime:
    settings: Settings
    engine: Engine
    sessions: sessionmaker
    catalog: Catalog
    limiter: RateLimiter
    koppeling_limiter: RateLimiter


def runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.runtime.sessions() as db:
        yield db
