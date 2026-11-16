"""Catalogus (`catalog/metrics.yaml`) en standaardmapping (`catalog/mapping.yaml`) laden en in de
database zetten. De bestanden zijn versiebeheerd; de database volgt de catalogus bij elke start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Department, Kap, LampConfig, Mapping, MetricCatalog, Site, SourceDevice

SCOPE_LEVELS = {"bedrijf", "afdeling", "kap"}


@dataclass(frozen=True)
class PrivaMatch:
    label: str
    type_1: str
    type_2: str | None = None
    idx_2: str | None = None

    def matches(self, row: dict) -> bool:
        if row["label"] != self.label or row["type_1"] != self.type_1:
            return False
        if self.type_2 is not None and row["type_2"] != self.type_2:
            return False
        return self.idx_2 is None or row["idx_2"] == self.idx_2


@dataclass
class Metric:
    key: str
    omschrijving: str
    rapport: str | None
    niveaus: list[str]
    eenheid: str
    aggregatie: str
    status: str
    priva: list[PrivaMatch]
    verwachte_eenheid: str | None = None
    plausibel: dict | None = None
    max_dagduur: bool = False
    kap_rollup: str | None = None

    @property
    def hoofdniveau(self) -> str:
        for level in ("bedrijf", "afdeling", "kap"):
            if level in self.niveaus:
                return level
        return f"bron:{self.niveaus[0]}"

    @property
    def bron_type(self) -> str | None:
        """Brontype van de ruwe waarde (module, doek, ...) of None als het een afdeling/bedrijf is."""
        for level in self.niveaus:
            if level not in SCOPE_LEVELS:
                return level
        return None


@dataclass
class Catalog:
    versie: int
    bron_types: dict[str, str]
    rapportfamilies: dict[str, dict]
    genegeerd: dict[str, dict]
    metrics: dict[str, Metric]
    mapping: dict = field(default_factory=dict)

    def match(self, row: dict) -> Metric | None:
        found = [m for m in self.metrics.values() if any(p.matches(row) for p in m.priva)]
        if len(found) > 1:
            raise ValueError(f"Catalogus dubbelzinnig voor label {row['label']}: {[m.key for m in found]}")
        return found[0] if found else None

    def supported(self, family: str) -> bool:
        return self.rapportfamilies.get(family, {}).get("ondersteund_vanaf_slice", 99) <= 1

    def family_name(self, family: str) -> str:
        return self.rapportfamilies.get(family, {}).get("naam", family)

    # --- verwachting (mapping.yaml) ---------------------------------------------------------
    def expected_departments(self) -> list[int]:
        return list(self.mapping.get("verwachting", {}).get("klimaat", {}).get("afdelingen", []))

    def ignored_departments(self) -> set[int]:
        result: set[int] = set()
        for rng in self.mapping.get("afdelingen_genegeerd", []) or []:
            result.update(range(int(rng["van"]), int(rng["tot"]) + 1))
        return result

    def expected_modules(self) -> set[int]:
        result: set[int] = set()
        for rng in self.mapping.get("verwachting", {}).get("belichting", {}).get("modules", []):
            result.update(range(int(rng["van"]), int(rng["tot"]) + 1))
        return result


def load_catalog(catalog_dir: Path) -> Catalog:
    raw = yaml.safe_load((catalog_dir / "metrics.yaml").read_text(encoding="utf-8"))
    mapping = yaml.safe_load((catalog_dir / "mapping.yaml").read_text(encoding="utf-8"))
    metrics: dict[str, Metric] = {}
    for item in raw["metrics"]:
        priva = [
            PrivaMatch(
                label=p["label"],
                type_1=p["type_1"],
                type_2=p.get("type_2"),
                idx_2=None if p.get("idx_2") is None else str(p["idx_2"]),
            )
            for p in item.get("priva") or []
        ]
        if item["key"] in metrics:
            raise ValueError(f"Dubbele metric_key in catalogus: {item['key']}")
        metrics[item["key"]] = Metric(
            key=item["key"],
            omschrijving=item["omschrijving"],
            rapport=item.get("rapport"),
            niveaus=list(item["niveaus"]),
            eenheid=item["eenheid"],
            aggregatie=item["aggregatie"],
            status=item["status"],
            priva=priva,
            verwachte_eenheid=item.get("verwachte_eenheid"),
            plausibel=item.get("plausibel"),
            max_dagduur=bool(item.get("max_dagduur", False)),
            kap_rollup=item.get("kap_rollup"),
        )
    genegeerd = {g["label"]: g for g in raw.get("genegeerd") or []}
    return Catalog(
        versie=int(raw["versie"]),
        bron_types=dict(raw["bron_types"]),
        rapportfamilies=dict(raw["rapportfamilies"]),
        genegeerd=genegeerd,
        metrics=metrics,
        mapping=mapping,
    )


def _d(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def sync_catalog(db: Session, catalog: Catalog) -> None:
    """Catalogus in de database bijwerken (upsert). Verwijderde metrics blijven staan voor historie."""
    existing = {m.metric_key: m for m in db.scalars(select(MetricCatalog))}
    for metric in catalog.metrics.values():
        row = existing.get(metric.key) or MetricCatalog(metric_key=metric.key)
        row.betekenis = metric.omschrijving
        row.canonieke_eenheid = metric.eenheid
        row.aggregatieregel = metric.aggregatie
        row.priva_namen = sorted({p.label for p in metric.priva})
        db.add(row)
    db.flush()


def seed_mapping(db: Session, catalog: Catalog) -> None:
    """Standaardmapping uit sectie 1.2 eenmalig in de database zetten (leeg → gevuld)."""
    if db.scalar(select(Site.id)) is not None:
        return
    cfg = catalog.mapping
    site = Site(naam=cfg["site"]["naam"], tijdzone=cfg["site"]["tijdzone"])
    db.add(site)
    db.flush()
    kap_cfg = cfg["kappen"]
    kap_from = _d(kap_cfg.get("geldig_vanaf"))
    kaps: dict[int, Kap] = {}
    for afd in cfg["afdelingen"]:
        dep = Department(
            site_id=site.id,
            nummer=int(afd["nummer"]),
            priva_code=str(afd["priva_code"]),
            naam=afd["naam"],
            status=afd["status"],
            geldig_vanaf=kap_from,
        )
        db.add(dep)
        db.flush()
        for nr in range(int(afd["kap_van"]), int(afd["kap_tot"]) + 1):
            kap = Kap(
                department_id=dep.id,
                kapnummer=nr,
                oppervlakte_m2=kap_cfg.get("oppervlakte_m2"),
                geldig_vanaf=kap_from,
            )
            db.add(kap)
            kaps[nr] = kap
    db.flush()
    if sorted(kaps) != list(range(1, int(kap_cfg["aantal"]) + 1)):
        raise ValueError("mapping.yaml: afdelingen dekken niet precies kap 1..aantal")

    def device(kind: str, nr: int) -> SourceDevice:
        dev = SourceDevice(type=kind, extern_id=nr)
        db.add(dev)
        db.flush()
        return dev

    for rng in cfg.get("kranen", []):
        for nr in range(int(rng["van"]), int(rng["tot"]) + 1):
            dev = device("kraan", nr)
            db.add(
                Mapping(
                    source_device_id=dev.id,
                    kap_id=kaps[nr - int(rng["kap_offset"])].id,
                    geldig_vanaf=_d(rng.get("geldig_vanaf")),
                    status=rng["status"],
                )
            )
    for rng in cfg.get("kranen_zonder_kap", []) or []:
        for nr in range(int(rng["van"]), int(rng["tot"]) + 1):
            dev = device("kraan", nr)
            dev.zone = rng["zone"]
            db.add(
                Mapping(
                    source_device_id=dev.id, geldig_vanaf=_d(rng.get("geldig_vanaf")), status=rng["status"]
                )
            )
    for rng in cfg.get("lichtmodules", []):
        for nr in range(int(rng["van"]), int(rng["tot"]) + 1):
            dev = device("module", nr)
            db.add(
                Mapping(
                    source_device_id=dev.id,
                    kap_id=kaps[nr - int(rng["kap_offset"])].id,
                    geldig_vanaf=_d(rng.get("geldig_vanaf")),
                    status=rng["status"],
                )
            )
            db.add(
                LampConfig(
                    source_device_id=dev.id,
                    umol=float(rng["umol"]),
                    dimbaar=bool(rng["dimbaar"]),
                    geldig_vanaf=_d(rng.get("geldig_vanaf")),
                )
            )
    db.flush()
