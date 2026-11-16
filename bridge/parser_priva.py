"""Parser voor Priva Office Direct CSV-rapporten (rapportage-export, dagwaarden).

Formaat (vastgesteld op de echte exports en de bestaande import van de gewichtsapp):
kopregel `label;pcu;type_1;idx_1;type_2;idx_2;startdate;enddate;value`, één regel per meetpunt
per dag. Puntkomma (tab ook toegestaan), UTF-8 (met of zonder BOM) met Windows-1252 als terugval,
decimale komma (punt ook toegestaan), datums `d-m-jj` of `d-m-jjjj` met begin = eind.

De rapportfamilie wordt op inhoud herkend, nooit op bestandsnaam.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, date, datetime, timedelta

from .config import TZ

HEADERS = ("label", "pcu", "type_1", "idx_1", "type_2", "idx_2", "startdate", "enddate", "value")
MAX_ROWS = 2_000_000
_NUMBER = re.compile(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)")
_DATE = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2}|\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")


class ImportRefused(Exception):
    """Het bestand wordt niet (verder) verwerkt. `code` is machineleesbaar, `detail` Nederlands."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def decode(data: bytes) -> tuple[str, str]:
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="strict"), "cp1252"


def read_rows(data: bytes) -> tuple[list[tuple[int, dict]], str, str]:
    """Geeft (regels met regelnummer in het bestand, encoding, scheidingsteken)."""
    if not data.strip():
        raise ImportRefused("empty_file", "Het bestand is leeg.")
    try:
        text, encoding = decode(data)
    except UnicodeDecodeError as exc:
        raise ImportRefused("encoding", "Tekenset niet herkend (verwacht UTF-8 of Windows-1252).") from exc
    first = text.split("\n", 1)[0]
    delimiter = "\t" if first.count("\t") > first.count(";") else ";"
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = next(reader, None)
    if header is None or tuple(h.strip() for h in header) != HEADERS:
        raise ImportRefused(
            "unknown_variant",
            "Onbekende rapportvariant: de kopregel moet "
            f"'{';'.join(HEADERS)}' zijn, maar is '{';'.join(header or [])}'.",
        )
    rows: list[tuple[int, dict]] = []
    for line_no, values in enumerate(reader, start=2):
        if not values or (len(values) == 1 and not values[0].strip()):
            continue
        if len(values) != len(HEADERS):
            raise ImportRefused(
                "unknown_variant",
                f"Onbekende rapportvariant: regel {line_no} heeft {len(values)} velden in plaats van "
                f"{len(HEADERS)}.",
            )
        if len(rows) >= MAX_ROWS:
            raise ImportRefused("too_large", f"Meer dan {MAX_ROWS} regels in één bestand.")
        rows.append((line_no, {k: v.strip() for k, v in zip(HEADERS, values, strict=True)}))
    if not rows:
        raise ImportRefused("empty_file", "Het bestand bevat alleen een kopregel.")
    return rows, encoding, delimiter


def parse_priva_date(value: str) -> date:
    """Lokale datum (Europe/Amsterdam). Tweecijferig jaar 00–69 = 2000–2069; daarbuiten 4 cijfers.

    Een tijdstip wordt geweigerd: slice 1 importeert alleen dagwaarden. `parse_priva_datetime`
    bewaakt de zomer-/wintertijd voor subdagelijkse waarden in een latere slice.
    """
    match = _DATE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"ongeldige datum '{value}'; verwacht d-m-jj of d-m-jjjj")
    if match.group(4) is not None:
        raise ValueError(f"datum '{value}' bevat een tijdstip; alleen dagwaarden worden ondersteund")
    d, m, y = (int(match.group(i)) for i in (1, 2, 3))
    if len(match.group(3)) == 2:
        if y > 69:
            raise ValueError(f"eeuw onzeker in '{value}'; lever een viercijferig jaar")
        y += 2000
    if not 2000 <= y <= 2099:
        raise ValueError(f"jaar buiten 2000–2099 in '{value}'")
    return date(y, m, d)


def parse_priva_datetime(value: str) -> datetime:
    """Lokale tijd → aware datetime; een niet-bestaand of dubbel tijdstip (DST) wordt geweigerd."""
    match = _DATE.fullmatch(value.strip())
    if not match or match.group(4) is None:
        raise ValueError(f"ongeldig tijdstip '{value}'")
    day = parse_priva_date(value.strip().split()[0].split("T")[0])
    naive = datetime(
        day.year, day.month, day.day, int(match.group(4)), int(match.group(5)), int(match.group(6) or 0)
    )
    local = naive.replace(tzinfo=TZ)
    if local.utcoffset() != naive.replace(tzinfo=TZ, fold=1).utcoffset():
        raise ValueError(f"tijdstip '{value}' is dubbel of bestaat niet (zomer-/wintertijd)")
    return local


def parse_value(raw: str) -> float | None:
    """Leeg → None (ontbreekt, nooit 0). Decimale komma of punt. Anders ValueError."""
    raw = raw.strip()
    if raw == "":
        return None
    if not _NUMBER.fullmatch(raw):
        raise ValueError(f"geen getal: '{raw}'")
    return float(raw.replace(",", "."))


def day_seconds(day: date) -> int:
    """Lengte van een lokale dag in seconden: 82800, 86400 of 90000 (zomer-/wintertijd)."""
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ)
    # Aware datetimes met dezelfde tzinfo worden naïef afgetrokken; daarom eerst naar UTC.
    return int((end.astimezone(UTC) - start.astimezone(UTC)).total_seconds())


def recognize_family(labels: set[str], types: set[str], departments: set[str]) -> str:
    """Rapportfamilie op inhoud (labels en objecttypes), getoetst op de vijf echte exports."""
    if any("Valve" in x for x in labels) and "Kraan" in types:
        return "kranen"
    if any("ModGrowlight" in x for x in labels) or "Groeilicht sturing" in types:
        return "belichting"
    if any(x.startswith("Sum_24h_Vg") or "MoistDrain" in x for x in labels):
        return "algemeen_afdeling"
    if any("CO2ProgActive" in x or x == "Sum_Day_Radiation" for x in labels):
        return "algemeen_bedrijf"
    if any("DewPointDifference" in x or "PAR_Growlight" in x for x in labels):
        return "klimaat"
    if len(departments) > 1:
        return "algemeen_bedrijf"
    if any("Comp" in x or "Curtain" in x for x in labels):
        return "klimaat"
    raise ImportRefused("unknown_family", "Onbekende rapportfamilie: de inhoud is niet betrouwbaar herkend.")
