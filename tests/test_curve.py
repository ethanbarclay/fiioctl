"""EQ response maths. Checked against the textbook behaviour of RBJ biquads."""

import pytest

from fiioctl import curve


def peak_at(freq, gain, q=1.0):
    return curve.response([{"freq": freq, "gain": gain, "q": q, "filter": "peak"}])


def test_flat_band_is_flat():
    _, db = peak_at(1000, 0.0)
    assert max(abs(v) for v in db) < 1e-6


def test_peak_reaches_its_gain():
    _, db = peak_at(1000, 6.0)
    assert max(db) == pytest.approx(6.0, abs=0.05)


def test_cut_reaches_its_attenuation():
    _, db = peak_at(1000, -6.0)
    assert min(db) == pytest.approx(-6.0, abs=0.05)


def test_peak_is_centred_on_its_frequency():
    freqs, db = peak_at(1000, 6.0, q=4.0)
    assert freqs[db.index(max(db))] == pytest.approx(1000, rel=0.1)


def test_higher_q_is_narrower():
    _, wide = peak_at(1000, 6.0, q=0.5)
    _, tight = peak_at(1000, 6.0, q=8.0)
    lifted = lambda db: sum(1 for v in db if v > 1.0)  # noqa: E731
    assert lifted(tight) < lifted(wide)


def test_low_shelf_lifts_bass_only():
    _, db = curve.response([{"freq": 105, "gain": 5.5, "q": 0.7, "filter": "low-shelf"}])
    assert db[0] == pytest.approx(5.5, abs=0.1)
    assert db[-1] == pytest.approx(0.0, abs=0.1)


def test_high_shelf_cuts_treble_only():
    _, db = curve.response([{"freq": 10000, "gain": -4.0, "q": 0.7, "filter": "high-shelf"}])
    assert db[0] == pytest.approx(0.0, abs=0.1)
    assert db[-1] == pytest.approx(-4.0, abs=0.2)


def test_low_pass_rolls_off_treble():
    _, db = curve.response([{"freq": 1000, "gain": 0.0, "q": 0.7, "filter": "low-pass"}])
    assert db[0] == pytest.approx(0.0, abs=0.1)
    assert db[-1] < -40


def test_high_pass_rolls_off_bass():
    _, db = curve.response([{"freq": 1000, "gain": 0.0, "q": 0.7, "filter": "high-pass"}])
    assert db[0] < -40
    assert db[-1] == pytest.approx(0.0, abs=0.1)


def test_bands_sum():
    bands = [{"freq": 1000, "gain": 3.0, "q": 1.0, "filter": "peak"},
             {"freq": 1000, "gain": 2.0, "q": 1.0, "filter": "peak"}]
    _, db = curve.response(bands)
    assert max(db) == pytest.approx(5.0, abs=0.1)


def test_preamp_offsets_everything():
    _, db = curve.response([{"freq": 1000, "gain": 0.0, "q": 1.0, "filter": "peak"}],
                           preamp=-6.0)
    assert all(v == pytest.approx(-6.0) for v in db)


def test_unknown_filter_type_is_treated_as_flat():
    _, db = curve.response([{"freq": 1000, "gain": 9.0, "q": 1.0, "filter": "nonsense"}])
    assert max(abs(v) for v in db) < 1e-6


def test_plot_shape_and_axis():
    bands = [{"freq": f, "gain": g, "q": 0.71, "filter": "peak"}
             for f, g in [(100, 4.0), (1000, -4.0)]]
    rows = curve.plot(bands, width=40, height=9)
    assert len(rows) == 10            # 9 plot rows plus the frequency axis
    assert "┼" in "".join(rows)       # zero line
    assert "20" in rows[-1] and "10k" in rows[-1]


def test_plot_survives_empty_bands():
    rows = curve.plot([], width=20, height=7)
    assert len(rows) == 8
