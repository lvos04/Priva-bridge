"""Datamodel volgens implementatieplan sectie 6.

`daily_aggregate` volgt in slice 2 (aggregator); in slice 1 rekent /daily direct op de
observaties, die al dagwaarden zijn.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Site(Base):
    __tablename__ = "site"
    id: Mapped[int] = mapped_column(primary_key=True)
    naam: Mapped[str] = mapped_column(String(100))
    tijdzone: Mapped[str] = mapped_column(String(64))


class Department(Base):
    __tablename__ = "department"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    nummer: Mapped[int] = mapped_column(Integer)
    priva_code: Mapped[str] = mapped_column(String(32))
    naam: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16))
    geldig_vanaf: Mapped[date | None] = mapped_column(Date)
    geldig_tot: Mapped[date | None] = mapped_column(Date)


class Kap(Base):
    __tablename__ = "kap"
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("department.id"))
    kapnummer: Mapped[int] = mapped_column(Integer, index=True)
    oppervlakte_m2: Mapped[float | None] = mapped_column(Float)
    geldig_vanaf: Mapped[date | None] = mapped_column(Date)
    geldig_tot: Mapped[date | None] = mapped_column(Date)


class SourceDevice(Base):
    __tablename__ = "source_device"
    __table_args__ = (UniqueConstraint("type", "extern_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))  # kraan, module, doek, doseerruimte, verwarming, groeilicht
    extern_id: Mapped[int] = mapped_column(Integer)
    zone: Mapped[str | None] = mapped_column(String(64))  # bijv. gevel, achtertuin (bron zonder kap)


class Mapping(Base):
    __tablename__ = "mapping"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_device_id: Mapped[int] = mapped_column(ForeignKey("source_device.id"), index=True)
    kap_id: Mapped[int | None] = mapped_column(ForeignKey("kap.id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("department.id"))
    geldig_vanaf: Mapped[date | None] = mapped_column(Date)
    geldig_tot: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))  # bevestigd, aangenomen, unmapped


class LampConfig(Base):
    __tablename__ = "lamp_config"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_device_id: Mapped[int] = mapped_column(ForeignKey("source_device.id"), index=True)
    umol: Mapped[float] = mapped_column(Float)
    dimbaar: Mapped[bool] = mapped_column(Boolean)
    geldig_vanaf: Mapped[date | None] = mapped_column(Date)
    geldig_tot: Mapped[date | None] = mapped_column(Date)


class MetricCatalog(Base):
    __tablename__ = "metric_catalog"
    metric_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    betekenis: Mapped[str] = mapped_column(Text)
    canonieke_eenheid: Mapped[str] = mapped_column(String(32))
    aggregatieregel: Mapped[str] = mapped_column(String(32))
    priva_namen: Mapped[list] = mapped_column(JSON)


class ImportBatch(Base):
    __tablename__ = "import_batch"
    id: Mapped[int] = mapped_column(primary_key=True)
    bestandsnaam: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    grootte: Mapped[int] = mapped_column(Integer)
    rapportfamilie: Mapped[str | None] = mapped_column(String(32))
    periode_van: Mapped[date | None] = mapped_column(Date)
    periode_tot: Mapped[date | None] = mapped_column(Date)
    # preview, verwerkt, geweigerd
    status: Mapped[str] = mapped_column(String(16), index=True)
    aantallen: Mapped[dict] = mapped_column(JSON, default=dict)
    meldingen: Mapped[list] = mapped_column(JSON, default=list)
    parserversie: Mapped[str] = mapped_column(String(32))
    bron: Mapped[str] = mapped_column(String(16), default="manual")
    ontvangen_op: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    verwerkt_op: Mapped[datetime | None] = mapped_column(DateTime)

    @property
    def import_id(self) -> str:
        return f"imp_{self.id:04d}"


class Observation(Base):
    """Eén dagwaarde van één bronpunt. Uniek op (punt, metric, datum, aggregatie, revisie).

    `point_key` is de scope-notatie van het bronpunt: `afdeling:1` voor klimaatwaarden per afdeling,
    `bron:module:112` voor een lichtmodule. Een afwijkende herimport geeft een nieuwe revisie.
    """

    __tablename__ = "observation"
    __table_args__ = (
        UniqueConstraint("point_key", "metric_key", "lokale_datum", "aggregation_kind", "revisie"),
        Index("ix_observation_lookup", "metric_key", "point_key", "lokale_datum"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    metric_key: Mapped[str] = mapped_column(ForeignKey("metric_catalog.metric_key"))
    point_key: Mapped[str] = mapped_column(String(64))
    scope_type: Mapped[str] = mapped_column(String(16))  # afdeling, bron
    scope_id: Mapped[int] = mapped_column(Integer)
    source_device_id: Mapped[int | None] = mapped_column(ForeignKey("source_device.id"))
    lokale_datum: Mapped[date] = mapped_column(Date, index=True)
    timestamp_utc: Mapped[datetime | None] = mapped_column(DateTime)
    aggregation_kind: Mapped[str] = mapped_column(String(32))
    waarde: Mapped[float | None] = mapped_column(Float)
    ruwe_waarde: Mapped[str] = mapped_column(String(64))
    kwaliteit: Mapped[str] = mapped_column(String(16))  # ok, verdacht, ontbreekt
    import_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"), index=True)
    revisie: Mapped[int] = mapped_column(Integer)
    aangemaakt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiToken(Base):
    __tablename__ = "api_token"
    id: Mapped[int] = mapped_column(primary_key=True)
    naam: Mapped[str] = mapped_column(String(100))
    voorvoegsel: Mapped[str] = mapped_column(String(12))
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list] = mapped_column(JSON)
    aangemaakt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    laatst_gebruikt: Mapped[datetime | None] = mapped_column(DateTime)
    ingetrokken_op: Mapped[datetime | None] = mapped_column(DateTime)


class Koppelsleutel(Base):
    """Bedrijfskoppeling API v1: los van de /api/v1-tokens (zie docs/koppeling-api.md)."""

    __tablename__ = "koppelsleutel"
    id: Mapped[int] = mapped_column(primary_key=True)
    naam: Mapped[str] = mapped_column(String(100))
    voorvoegsel: Mapped[str] = mapped_column(String(8))
    sleutel_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    aangemaakt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    aangemaakt_door: Mapped[str] = mapped_column(String(32))
    laatst_gebruikt_op: Mapped[datetime | None] = mapped_column(DateTime)
    ingetrokken_op: Mapped[datetime | None] = mapped_column(DateTime)


class Instelling(Base):
    __tablename__ = "instelling"
    sleutel: Mapped[str] = mapped_column(String(64), primary_key=True)
    waarde: Mapped[str] = mapped_column(Text)
    gewijzigd_op: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
