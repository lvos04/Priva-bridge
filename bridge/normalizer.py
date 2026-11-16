"""Normalizer: Priva-regels → kandidaat-observaties via catalogus en mapping.

Alle problemen worden verzameld; is er minstens één fout, dan wordt het hele bestand geweigerd
(er verdwijnen nooit stilzwijgend records). Overgeslagen labels staan in de catalogus en tellen
zichtbaar mee in de preview.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from .catalog import Catalog
from .mapping import Structure
from .parser_priva import (
    ImportRefused,
    day_seconds,
    parse_priva_date,
    parse_value,
    read_rows,
    recognize_family,
)

MAX_MELDINGEN = 25


@dataclass
class Candidate:
    point_key: str
    scope_type: str  # afdeling | bron
    bron_type: str | None
    nummer: int
    metric_key: str
    lokale_datum: date
    aggregation_kind: str
    waarde: float | None
    ruwe_waarde: str
    kwaliteit: str  # ok | verdacht | ontbreekt
    regel: int


@dataclass
class Melding:
    code: str
    detail: str

    def as_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}


@dataclass
class Analysis:
    family: str | None = None
    encoding: str | None = None
    delimiter: str | None = None
    periode_van: date | None = None
    periode_tot: date | None = None
    candidates: list[Candidate] = field(default_factory=list)
    errors: list[Melding] = field(default_factory=list)
    warnings: list[Melding] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _limited(items: list, n: int = 8) -> str:
    text = ", ".join(str(i) for i in items[:n])
    return text + (f" … (+{len(items) - n})" if len(items) > n else "")


def analyse(data: bytes, catalog: Catalog, structure: Structure) -> Analysis:
    result = Analysis()
    try:
        rows, result.encoding, delimiter = read_rows(data)
        result.delimiter = "tab" if delimiter == "\t" else "puntkomma"
        labels = {r["label"] for _, r in rows}
        types = {r["type_1"] for _, r in rows}
        # Afdelingen die volgens de mapping genegeerd worden, tellen niet mee in de herkenning.
        ignored = {str(n) for n in catalog.ignored_departments()}
        departments = {r["idx_1"] for _, r in rows if r["type_1"] == "Afdeling" and r["idx_1"] not in ignored}
        result.family = recognize_family(labels, types, departments)
    except ImportRefused as exc:
        result.errors.append(Melding(exc.code, exc.detail))
        return result
    result.counts["regels"] = len(rows)
    if not catalog.supported(result.family):
        slice_nr = catalog.rapportfamilies.get(result.family, {}).get("ondersteund_vanaf_slice", "?")
        result.errors.append(
            Melding(
                "unsupported_family",
                f"Rapport herkend als {catalog.family_name(result.family)}; deze rapportfamilie wordt "
                f"pas vanaf slice {slice_nr} geïmporteerd. Het bestand is niet verwerkt.",
            )
        )
        return result

    afdelingen = set(structure.afdeling_numbers())
    genegeerde_afdelingen = catalog.ignored_departments()
    overgeslagen_afdelingen: Counter = Counter()
    unknown_columns: dict[tuple, list[int]] = defaultdict(list)
    unknown_types: dict[str, list[int]] = defaultdict(list)
    bad_dates: list[str] = []
    bad_values: list[str] = []
    bad_ignored: list[str] = []
    bad_numbers: list[str] = []
    stats: Counter = Counter()
    dates: set[date] = set()
    seen: dict[tuple, Candidate] = {}
    conflicts: list[str] = []
    unmapped: set[str] = set()
    afdelingen_gezien: set[int] = set()

    for line_no, row in rows:
        if row["type_1"] not in catalog.bron_types:
            unknown_types[row["type_1"] or "(leeg)"].append(line_no)
            continue
        ignored = catalog.genegeerd.get(row["label"])
        if ignored is not None:
            if row["value"] in [str(v) for v in ignored.get("toegestaan", [""])]:
                stats["overgeslagen"] += 1
            else:
                bad_ignored.append(f"regel {line_no} ({row['label']}='{row['value']}')")
            continue
        try:
            metric = catalog.match(row)
        except ValueError as exc:
            result.errors.append(Melding("catalog_ambiguous", str(exc)))
            return result
        if metric is None:
            key = (row["label"], row["type_1"], row["type_2"], row["idx_2"])
            unknown_columns[key].append(line_no)
            continue
        try:
            start = parse_priva_date(row["startdate"])
            end = parse_priva_date(row["enddate"])
        except ValueError as exc:
            bad_dates.append(f"regel {line_no}: {exc}")
            continue
        if start != end:
            bad_dates.append(
                f"regel {line_no}: begin {row['startdate']} ≠ eind {row['enddate']} (geen dagwaarde)"
            )
            continue
        try:
            value = parse_value(row["value"])
        except ValueError as exc:
            bad_values.append(f"regel {line_no}: {exc}")
            continue
        if not row["idx_1"].isdigit():
            bad_numbers.append(f"regel {line_no}: objectnummer '{row['idx_1']}'")
            continue
        nummer = int(row["idx_1"])
        bron_type = catalog.bron_types[row["type_1"]]
        if bron_type == "afdeling":
            if nummer in genegeerde_afdelingen:
                overgeslagen_afdelingen[nummer] += 1
                continue
            if nummer not in afdelingen:
                bad_numbers.append(f"regel {line_no}: afdeling {nummer} bestaat niet in de kasindeling")
                continue
            point_key, scope_type, cand_bron = f"afdeling:{nummer}", "afdeling", None
            afdelingen_gezien.add(nummer)
        else:
            point_key, scope_type, cand_bron = f"bron:{bron_type}:{nummer}", "bron", bron_type
            if structure.resolve(bron_type, nummer, start).status == "unmapped":
                unmapped.add(point_key)
        if metric.bron_type not in (None, cand_bron) or (metric.bron_type is None and cand_bron is not None):
            unknown_columns[(row["label"], row["type_1"], row["type_2"], row["idx_2"])].append(line_no)
            continue

        quality = "ok"
        if value is None:
            quality = "ontbreekt"
            stats["leeg"] += 1
        else:
            if value == 0:
                stats["nul"] += 1
            limits = metric.plausibel or {}
            if ("min" in limits and value < limits["min"]) or ("max" in limits and value > limits["max"]):
                quality = "verdacht"
            if metric.max_dagduur and not 0 <= value <= day_seconds(start):
                quality = "verdacht"
            if quality == "verdacht":
                stats["verdacht"] += 1
        cand = Candidate(
            point_key=point_key,
            scope_type=scope_type,
            bron_type=cand_bron,
            nummer=nummer,
            metric_key=metric.key,
            lokale_datum=start,
            aggregation_kind=metric.aggregatie,
            waarde=value,
            ruwe_waarde=row["value"],
            kwaliteit=quality,
            regel=line_no,
        )
        ident = (point_key, metric.key, start, metric.aggregatie)
        previous = seen.get(ident)
        if previous is not None:
            if previous.waarde != value:
                conflicts.append(
                    f"{point_key} {metric.key} {start.isoformat()}: regel {previous.regel} "
                    f"('{previous.ruwe_waarde}') en regel {line_no} ('{row['value']}')"
                )
            else:
                stats["dubbel_in_bestand"] += 1
            continue
        seen[ident] = cand
        dates.add(start)

    for key, lines in sorted(unknown_columns.items()):
        label, type_1, type_2, idx_2 = key
        extra = f", {type_2} {idx_2}" if type_2 or idx_2 else ""
        result.errors.append(
            Melding(
                "unknown_column",
                f"{catalog.family_name(result.family)}: kolom '{label}' ({type_1}{extra}) niet in catalogus "
                f"({len(lines)} regels, eerste regel {lines[0]}).",
            )
        )
    for type_1, lines in sorted(unknown_types.items()):
        result.errors.append(
            Melding(
                "unknown_variant",
                f"Onbekende rapportvariant: objecttype '{type_1}' niet in catalogus "
                f"({len(lines)} regels, eerste regel {lines[0]}).",
            )
        )
    for code, items, text in (
        ("invalid_date", bad_dates, "Ongeldige datum"),
        ("invalid_value", bad_values, "Ongeldige waarde"),
        ("unexpected_value", bad_ignored, "Waarde in een kolom die als leeg bekend staat"),
        ("unknown_object", bad_numbers, "Onbekend object"),
        ("conflicting_duplicate", conflicts, "Tegenstrijdige dubbele regels"),
    ):
        if items:
            result.errors.append(Melding(code, f"{text}: {_limited(items)}"))
    result.errors = result.errors[:MAX_MELDINGEN]

    result.candidates = list(seen.values())
    if dates:
        result.periode_van, result.periode_tot = min(dates), max(dates)
    if unmapped:
        result.warnings.append(
            Melding(
                "unmapped_source",
                f"{len(unmapped)} bronnen zonder kap/afdeling (opgeslagen als unmapped): "
                f"{_limited(sorted(unmapped, key=_natural))}",
            )
        )
    if overgeslagen_afdelingen:
        result.warnings.append(
            Melding(
                "afdeling_genegeerd",
                f"Overgeslagen: afdeling {_ranges(sorted(overgeslagen_afdelingen))}, "
                "genegeerd volgens mapping "
                f"({sum(overgeslagen_afdelingen.values())} regels).",
            )
        )
    result.counts.update(
        {
            "overgeslagen_afdelingen": _ranges(sorted(overgeslagen_afdelingen)),
            "overgeslagen_afdeling_regels": sum(overgeslagen_afdelingen.values()),
            "waarden": len(result.candidates),
            "leeg": stats["leeg"],
            "nul": stats["nul"],
            "verdacht": stats["verdacht"],
            "overgeslagen": stats["overgeslagen"],
            "dubbel_in_bestand": stats["dubbel_in_bestand"],
            "dagen": len(dates),
            "afdelingen": sorted(afdelingen_gezien),
            "bronnen": _summarise_sources(result.candidates),
            "metrics": len({c.metric_key for c in result.candidates}),
        }
    )
    return result


def _natural(point_key: str):
    head, _, tail = point_key.rpartition(":")
    return (head, int(tail) if tail.isdigit() else 0)


def _summarise_sources(candidates: list[Candidate]) -> dict[str, str]:
    per_type: dict[str, set[int]] = defaultdict(set)
    for c in candidates:
        if c.bron_type:
            per_type[c.bron_type].add(c.nummer)
    return {k: _ranges(sorted(v)) for k, v in sorted(per_type.items())}


def _ranges(numbers: list[int]) -> str:
    parts, start, prev = [], None, None
    for n in numbers:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            parts.append(f"{start}–{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        parts.append(f"{start}–{prev}" if start != prev else str(start))
    return ", ".join(parts)
