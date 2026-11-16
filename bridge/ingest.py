"""Importpijplijn: bronadapter → raw_store → ingestion_ledger → parser/normalizer → observaties.

- `raw_store`: elk ontvangen bestand wordt ongewijzigd bewaard onder zijn SHA-256 in /data/raw.
- `ingestion_ledger`: één `import_batch` per uniek bestand (SHA-256); een al verwerkt bestand wordt
  niet opnieuw verwerkt. Op meetpuntniveau voorkomt de revisieregel dubbele observaties.
- Bronadapters (`source_manual` nu, `source_smb` in slice 4) leveren alleen (naam, bytes) aan
  `receive`; de rest van de pijplijn is voor elke bron gelijk.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import PARSER_VERSIE
from .catalog import Catalog
from .config import Settings
from .mapping import Structure, get_or_create_device
from .models import ImportBatch, Observation, utcnow
from .normalizer import Analysis, analyse


class SourceAdapter(Protocol):
    """Interface voor bronnen. Een bron levert bestanden; hij schrijft nooit naar Priva."""

    mode: str

    def submit(self, filename: str, data: bytes) -> ReceiveResult: ...


# --- raw_store ---------------------------------------------------------------------------------
def raw_path(settings: Settings, sha: str) -> Path:
    return settings.raw_dir / sha[:2] / f"{sha}.csv"


def store_raw(settings: Settings, data: bytes) -> str:
    """Bestand via staging atomair in het archief zetten; bestaand archief wordt nooit overschreven."""
    sha = hashlib.sha256(data).hexdigest()
    target = raw_path(settings, sha)
    if target.exists():
        return sha
    target.parent.mkdir(parents=True, exist_ok=True)
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=settings.staging_dir, prefix="upload-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        target.chmod(0o440)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return sha


def load_raw(settings: Settings, sha: str) -> bytes:
    data = raw_path(settings, sha).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise RuntimeError(f"Archiefbestand {sha} is gewijzigd; import afgebroken")
    return data


# --- ledger en verwerking ----------------------------------------------------------------------
@dataclass
class ReceiveResult:
    batch: ImportBatch
    analysis: Analysis | None
    duplicate: bool


def _meldingen(analysis: Analysis) -> list[dict]:
    return [{"niveau": "fout", **m.as_dict()} for m in analysis.errors] + [
        {"niveau": "waarschuwing", **m.as_dict()} for m in analysis.warnings
    ]


def receive(
    db: Session, settings: Settings, catalog: Catalog, filename: str, data: bytes, bron: str = "manual"
) -> ReceiveResult:
    """Bestand ontvangen en analyseren (preview). Er worden nog geen observaties geschreven."""
    sha = hashlib.sha256(data).hexdigest()
    batch = db.scalar(select(ImportBatch).where(ImportBatch.sha256 == sha))
    if batch is not None and batch.status == "verwerkt":
        return ReceiveResult(batch, None, True)
    store_raw(settings, data)
    analysis = analyse(data, catalog, Structure(db))
    if batch is None:
        batch = ImportBatch(sha256=sha, grootte=len(data), bron=bron)
        db.add(batch)
    batch.bestandsnaam = Path(filename).name[:255] or "upload.csv"
    batch.rapportfamilie = analysis.family
    batch.periode_van, batch.periode_tot = analysis.periode_van, analysis.periode_tot
    batch.status = "preview" if analysis.ok else "geweigerd"
    batch.aantallen = {
        **analysis.counts,
        "encoding": analysis.encoding,
        "scheidingsteken": analysis.delimiter,
    }
    batch.meldingen = _meldingen(analysis)
    batch.parserversie = PARSER_VERSIE
    batch.ontvangen_op = utcnow()
    db.commit()
    return ReceiveResult(batch, analysis, False)


def commit_batch(db: Session, settings: Settings, catalog: Catalog, batch_id: int) -> ImportBatch:
    """Een batch in preview verwerken. Idempotent: een verwerkte batch blijft ongewijzigd."""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise LookupError("Import niet gevonden")
    if batch.status == "verwerkt":
        return batch
    if batch.status != "preview":
        raise ValueError("Alleen een import met een geldige preview kan worden verwerkt")
    data = load_raw(settings, batch.sha256)
    analysis = analyse(data, catalog, Structure(db))
    if not analysis.ok:
        batch.status = "geweigerd"
        batch.meldingen = _meldingen(analysis)
        db.commit()
        return batch

    points = {c.point_key for c in analysis.candidates}
    metrics = {c.metric_key for c in analysis.candidates}
    latest: dict[tuple, Observation] = {}
    if analysis.candidates:
        existing = db.scalars(
            select(Observation).where(
                Observation.point_key.in_(points),
                Observation.metric_key.in_(metrics),
                Observation.lokale_datum >= analysis.periode_van,
                Observation.lokale_datum <= analysis.periode_tot,
            )
        )
        for obs in existing:
            ident = (obs.point_key, obs.metric_key, obs.lokale_datum, obs.aggregation_kind)
            if ident not in latest or obs.revisie > latest[ident].revisie:
                latest[ident] = obs

    device_ids: dict[tuple[str, int], int] = {}
    counts = {"nieuw": 0, "nieuwe_revisie": 0, "al_aanwezig": 0, "leeg_niet_overschreven": 0}
    for cand in analysis.candidates:
        ident = (cand.point_key, cand.metric_key, cand.lokale_datum, cand.aggregation_kind)
        current = latest.get(ident)
        if current is not None:
            if current.waarde == cand.waarde and current.kwaliteit == cand.kwaliteit:
                counts["al_aanwezig"] += 1
                continue
            if cand.waarde is None and current.waarde is not None:
                # Leeg is niet nul én wist geen bekende waarde: de bestaande waarde blijft gelden.
                counts["leeg_niet_overschreven"] += 1
                continue
        device_id = None
        if cand.bron_type:
            key = (cand.bron_type, cand.nummer)
            if key not in device_ids:
                device_ids[key] = get_or_create_device(db, cand.bron_type, cand.nummer).id
            device_id = device_ids[key]
        obs = Observation(
            metric_key=cand.metric_key,
            point_key=cand.point_key,
            scope_type=cand.scope_type,
            scope_id=cand.nummer if cand.scope_type == "afdeling" else device_id,
            source_device_id=device_id,
            lokale_datum=cand.lokale_datum,
            timestamp_utc=None,
            aggregation_kind=cand.aggregation_kind,
            waarde=cand.waarde,
            ruwe_waarde=cand.ruwe_waarde[:64],
            kwaliteit=cand.kwaliteit,
            import_id=batch.id,
            revisie=1 if current is None else current.revisie + 1,
        )
        db.add(obs)
        counts["nieuw" if current is None else "nieuwe_revisie"] += 1
    batch.status = "verwerkt"
    batch.verwerkt_op = utcnow()
    batch.aantallen = {**(batch.aantallen or {}), **counts}
    db.commit()
    return batch


class ManualSource:
    """`source_manual`: upload via de beheer-UI, met preview vóór verwerking."""

    mode = "manual"

    def __init__(self, db: Session, settings: Settings, catalog: Catalog):
        self.db, self.settings, self.catalog = db, settings, catalog

    def submit(self, filename: str, data: bytes) -> ReceiveResult:
        return receive(self.db, self.settings, self.catalog, filename, data, bron=self.mode)
