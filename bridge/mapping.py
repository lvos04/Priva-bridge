"""Mapping van bronapparaten (kraan, module, ...) naar kap en afdeling, met geldigheid."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Department, Kap, Mapping, SourceDevice


def _valid(row, on: date) -> bool:
    return (row.geldig_vanaf is None or row.geldig_vanaf <= on) and (
        row.geldig_tot is None or on <= row.geldig_tot
    )


@dataclass
class Resolved:
    kap: int | None
    afdeling: int | None
    status: str  # bevestigd, aangenomen, unmapped
    zone: str | None = None


class Structure:
    """Momentopname van kasindeling en mapping uit de database (één per verzoek of import)."""

    def __init__(self, db: Session):
        self.departments = {d.id: d for d in db.scalars(select(Department))}
        self.kaps = {k.id: k for k in db.scalars(select(Kap))}
        self.devices = {d.id: d for d in db.scalars(select(SourceDevice))}
        self.device_by_key = {(d.type, d.extern_id): d for d in self.devices.values()}
        self.mappings: dict[int, list[Mapping]] = {}
        for m in db.scalars(select(Mapping)):
            self.mappings.setdefault(m.source_device_id, []).append(m)

    def afdeling_numbers(self) -> list[int]:
        return sorted(d.nummer for d in self.departments.values())

    def kap_numbers(self) -> list[int]:
        return sorted({k.kapnummer for k in self.kaps.values()})

    def afdeling_of_kap(self, kapnummer: int, on: date) -> int | None:
        for kap in self.kaps.values():
            if kap.kapnummer == kapnummer and _valid(kap, on):
                return self.departments[kap.department_id].nummer
        return None

    def resolve(self, kind: str, nummer: int, on: date) -> Resolved:
        device = self.device_by_key.get((kind, nummer))
        if device is None:
            return Resolved(None, None, "unmapped")
        for m in self.mappings.get(device.id, []):
            if not _valid(m, on) or m.status == "unmapped":
                continue
            if m.kap_id is not None:
                kap = self.kaps[m.kap_id]
                return Resolved(kap.kapnummer, self.afdeling_of_kap(kap.kapnummer, on), m.status)
            if m.department_id is not None:
                return Resolved(None, self.departments[m.department_id].nummer, m.status)
            # Bevestigd bronapparaat zonder kap of afdeling (bijv. gevelkraan): eigen zone.
            return Resolved(None, None, m.status, device.zone)
        return Resolved(None, None, "unmapped")

    def devices_for_kap(self, kind: str, kapnummer: int, on: date) -> list[int]:
        result = []
        for device in self.devices.values():
            if device.type != kind:
                continue
            resolved = self.resolve(kind, device.extern_id, on)
            if resolved.kap == kapnummer:
                result.append(device.extern_id)
        return sorted(result)


def get_or_create_device(db: Session, kind: str, nummer: int) -> SourceDevice:
    """Bronapparaat opzoeken; een onbekend apparaat wordt aangemaakt met mapping `unmapped`."""
    device = db.scalar(
        select(SourceDevice).where(SourceDevice.type == kind, SourceDevice.extern_id == nummer)
    )
    if device is None:
        device = SourceDevice(type=kind, extern_id=nummer)
        db.add(device)
        db.flush()
        db.add(Mapping(source_device_id=device.id, status="unmapped"))
        db.flush()
    return device
