"""Vaste mapping uit sectie 1.2 plus antwoorden van Leon (16-11-2026)."""

from datetime import date

import pytest

from bridge.mapping import Structure

DAG = date(2026, 11, 15)


@pytest.fixture
def structure(rt):
    with rt.sessions() as db:
        return Structure(db)


@pytest.mark.parametrize(
    "kap,afdeling", [(1, 1), (9, 1), (10, 2), (18, 2), (19, 3), (30, 3), (31, 4), (40, 4)]
)
def test_kap_naar_afdeling(structure, kap, afdeling):
    assert structure.afdeling_of_kap(kap, DAG) == afdeling


def test_vier_afdelingen_en_veertig_kappen(structure):
    assert structure.afdeling_numbers() == [1, 2, 3, 4]
    assert structure.kap_numbers() == list(range(1, 41))


@pytest.mark.parametrize("module,kap", [(1, 1), (12, 12), (40, 40), (101, 1), (112, 12), (140, 40)])
def test_lichtmodules_1_40_en_101_140(structure, module, kap):
    resolved = structure.resolve("module", module, DAG)
    assert (resolved.kap, resolved.status) == (kap, "bevestigd")


@pytest.mark.parametrize("module", [0, 41, 100, 141])
def test_andere_modules_unmapped(structure, module):
    assert structure.resolve("module", module, DAG).status == "unmapped"


def test_kap_heeft_beide_lamptypes(structure):
    assert structure.devices_for_kap("module", 12, DAG) == [12, 112]


@pytest.mark.parametrize("kraan", [1, 20, 40])
def test_kraan_1_40_naar_kap(structure, kraan):
    resolved = structure.resolve("kraan", kraan, DAG)
    assert resolved.kap == kraan and resolved.afdeling == structure.afdeling_of_kap(kraan, DAG)


@pytest.mark.parametrize("kraan,zone", [(41, "gevel"), (44, "gevel"), (45, "achtertuin")])
def test_kraan_boven_40_bekend_zonder_kap(structure, kraan, zone):
    resolved = structure.resolve("kraan", kraan, DAG)
    assert (resolved.kap, resolved.afdeling, resolved.status, resolved.zone) == (
        None,
        None,
        "bevestigd",
        zone,
    )
    assert kraan not in structure.devices_for_kap("kraan", 40, DAG)


def test_kraan_46_onbekend_is_unmapped(structure):
    assert structure.resolve("kraan", 46, DAG).status == "unmapped"


def test_mapping_niet_geldig_voor_aug_2024(structure):
    assert structure.resolve("module", 112, date(2024, 7, 31)).status == "unmapped"
