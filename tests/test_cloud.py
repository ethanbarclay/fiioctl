"""FiiO catalogue helpers. Pure functions only - no network."""

import pytest

from fiioctl import cloud


def test_preset_rows_convert_to_profiles():
    row = {
        "id": 2, "styleName": "FH3 FOR KA15", "description": " nice ",
        "deviceType": 110, "masterGain": 8.0, "downloadSum": 27,
        "eqParamsJson": [
            {"position": 0, "frequency": 20, "gain": -3.6, "qValue": 1, "filterType": 0},
            {"position": 1, "frequency": 10000, "gain": 2.0, "qValue": 0.7, "filterType": 2},
        ],
    }
    out = cloud._as_profile(row, "fiio-official")
    assert out["name"] == "FH3 FOR KA15"
    assert out["source"] == "fiio-official"
    assert out["preamp"] == 8.0
    assert out["downloads"] == 27
    assert out["description"] == "nice"
    assert out["bands"][0] == {"freq": 20, "gain": -3.6, "q": 1.0, "filter": "peak"}
    assert out["bands"][1]["filter"] == "high-shelf"   # filterType 2


def test_missing_q_falls_back_rather_than_dividing_by_zero():
    row = {"styleName": "x", "eqParamsJson": [
        {"frequency": 100, "gain": 1.0, "qValue": 0, "filterType": 0}]}
    assert cloud._as_profile(row, "s")["bands"][0]["q"] == 0.7


def test_split_fr_into_parallel_arrays():
    assert cloud._split([[20.0, 1.5], [40.0, -2.0]]) == {
        "frequency": [20.0, 40.0], "raw": [1.5, -2.0]}


MEASUREMENTS = [
    {"id": 714, "productName": "Beyerdynamic DT 177X Go",
     "config": "leather earpads", "source": "oratory1990"},
    {"id": 715, "productName": "Beyerdynamic DT 177X Go",
     "config": "velour earpads", "source": "oratory1990"},
    {"id": 711, "productName": "Beyerdynamic DT 1770", "config": None,
     "source": "oratory1990"},
    {"id": 1, "productName": "Flat", "config": None, "source": ""},
]


def test_measurement_search_finds_both_earpad_variants():
    hits = cloud.find_measurements("dt 177x", entries=MEASUREMENTS)
    assert [h["id"] for h in hits] == [714, 715]


def test_measurement_search_ignores_spacing_and_case():
    for query in ("dt177x", "DT177X GO", "dt 177x go"):
        hits = cloud.find_measurements(query, entries=MEASUREMENTS)
        assert {h["id"] for h in hits} == {714, 715}, query


def test_measurement_search_prefers_exact_names():
    hits = cloud.find_measurements("flat", entries=MEASUREMENTS)
    assert hits[0]["productName"] == "Flat"


def test_measurement_search_rejects_empty_query():
    assert cloud.find_measurements("   ", entries=MEASUREMENTS) == []


LABELS = {"Harman over-ear 2018": 6, "Harman in-ear 2019": 5, "AutoEq in-ear": 2}
COMPAT = [
    {"label": "AutoEq in-ear",
     "recommended": [{"source": "crinacle", "form": "in-ear"}],
     "compatible": [{"source": "oratory1990", "form": "in-ear"}]},
    {"label": "Harman over-ear 2018",
     "recommended": [{"source": "oratory1990", "form": "over-ear"}],
     "compatible": []},
]


def test_recommended_target_uses_fiios_own_mapping():
    meas = {"source": "oratory1990", "formFactor": "over-ear"}
    assert cloud.recommended_target(meas, LABELS, COMPAT) == (6, "Harman over-ear 2018")


def test_recommended_target_falls_back_to_compatible():
    meas = {"source": "oratory1990", "formFactor": "in-ear"}
    assert cloud.recommended_target(meas, LABELS, COMPAT) == (2, "AutoEq in-ear")


def test_recommended_target_falls_back_by_form_factor():
    meas = {"source": "nobody", "formFactor": "in-ear"}
    assert cloud.recommended_target(meas, LABELS, COMPAT) == (5, "Harman in-ear 2019")


def test_ten_bands_maps_to_eight_peaking_plus_two_shelves():
    """8_PEAKING_WITH_SHELVES yields exactly 10 filters, which most models expose."""
    assert cloud.EQ_CONFIGS[10] == "8_PEAKING_WITH_SHELVES"


def test_presets_refuses_a_device_with_no_catalogue_id():
    with pytest.raises(cloud.CloudError):
        cloud.presets(None)
