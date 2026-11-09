"""Device layer: write failures, input validation, device selection. No hardware."""

from types import SimpleNamespace

import pytest

from fiioctl import cli, device, registry
from fiioctl.device import Dac, FiioError


class FakeHandle:
    """Answers hid_write the way cython-hidapi does: a byte count, or -1."""

    def __init__(self, rejects=()):
        self.rejects = rejects
        self.sent = []

    def write(self, out):
        self.sent.append(bytes(out))
        return -1 if len(out) in self.rejects else len(out)


def _wired(handle, spec=registry.KA15):
    return Dac(handle, {"product_string": spec.name}, spec)


def _dry(spec=registry.KA15):
    return Dac(None, {"product_string": spec.name}, spec, dry=True)


def _wire_value(capsys):
    """The value byte of the single frame a dry run just printed."""
    out, err = capsys.readouterr()
    return bytes.fromhex((out + err).split("TX ")[1])[7]


# -- write failures -----------------------------------------------------------


def test_rejected_length_falls_through_to_the_next():
    handle = FakeHandle(rejects=(64,))
    _wired(handle).send(2, b"\x01")
    assert [len(p) for p in handle.sent] == [64, 65]


def test_working_length_is_remembered():
    handle = FakeHandle(rejects=(64,))
    dac = _wired(handle)
    dac.send(2, b"\x01")
    handle.sent.clear()
    dac._last = 0.0
    dac.send(2, b"\x02")
    assert [len(p) for p in handle.sent] == [65]


def test_write_that_fails_at_every_length_raises():
    # the unpadded attempt is 10 bytes here: report id plus a 9-byte frame
    handle = FakeHandle(rejects=(64, 65, 33, 10))
    with pytest.raises(FiioError, match="write failed"):
        _wired(handle).send(2, b"\x01")


# -- value validation ---------------------------------------------------------


@pytest.mark.parametrize("bad", [-1, 256, 300])
def test_u8_with_no_declared_range_does_not_wrap(bad):
    with pytest.raises(FiioError, match="outside 0..255"):
        _dry().set("volume", bad)


def test_u8_limits_are_inclusive():
    assert _dry().set("volume", 0) == 0
    assert _dry().set("volume", 255) == 255


@pytest.mark.parametrize("bad", ["inf", "nan"])
def test_non_finite_is_rejected_cleanly(bad):
    with pytest.raises(FiioError, match="whole number"):
        _dry().set("volume", bad)


@pytest.mark.parametrize("kwargs, message", [
    ({"freq": 70000}, "frequency"),
    ({"freq": 0}, "frequency"),
    ({"freq": float("nan")}, "frequency"),
    ({"gain": 13}, "gain"),
    ({"q": 21}, "Q"),
    ({"q": 0.0}, "Q"),
    ({"index": -1}, "index"),
    ({"index": 256}, "index"),
    ({"index": 1.5}, "index"),
])
def test_band_fields_are_range_checked(kwargs, message):
    args = {"index": 0, "freq": 1000, "gain": 0.0, "q": 0.7, **kwargs}
    with pytest.raises(FiioError, match=message):
        _dry().set_band(args["index"], args["freq"], args["gain"], args["q"])


def test_band_index_respects_a_declared_band_count():
    ja11 = registry.DEVICES["JADEAUDIO JA11"]
    _dry(ja11).set_band(4, 1000, 0.0, 0.7)
    with pytest.raises(FiioError, match="outside 0..4"):
        _dry(ja11).set_band(5, 1000, 0.0, 0.7)


@pytest.mark.parametrize("word", ["enabled", "on", "1", "Yes", "y", True])
def test_bool_accepts_on_words(word, capsys):
    assert _dry().set("mic-switch", word) is True
    assert _wire_value(capsys) == 1


@pytest.mark.parametrize("word", ["disabled", "off", "0", "No", "n", False])
def test_bool_accepts_off_words(word, capsys):
    assert _dry().set("mic-switch", word) is False
    assert _wire_value(capsys) == 0


@pytest.mark.parametrize("word", ["enbaled", "2", "maybe", ""])
def test_bool_refuses_anything_else(word, capsys):
    with pytest.raises(FiioError, match="mic-switch: expected on or off"):
        _dry().set("mic-switch", word)
    assert "TX" not in "".join(capsys.readouterr())


# -- device selection ---------------------------------------------------------


def _enumerating(monkeypatch, *ids):
    infos = [{"vendor_id": v, "product_id": p, "usage_page": 0xFF00} for v, p in ids]
    monkeypatch.setattr(device, "hid", SimpleNamespace(enumerate=lambda: infos))


def test_any_product_from_fiio_itself_is_a_candidate(monkeypatch):
    _enumerating(monkeypatch, (0x2972, 0x0104), (0x2972, 0x9999))
    assert len(device.candidates()) == 2


@pytest.mark.parametrize("ids", [(0x3302, 15296), (0x3302, 15298), (0x0A12, 16391),
                                 (0x0666, 2184), (0x31B2, 65528)])
def test_rebadged_controllers_match_on_their_listed_product(monkeypatch, ids):
    _enumerating(monkeypatch, ids)
    assert len(device.candidates()) == 1


@pytest.mark.parametrize("ids", [(0x3302, 15299), (0x0A12, 1), (0x0666, 1), (0x31B2, 1),
                                 (0x1234, 16391)])
def test_other_products_from_those_vendors_are_ignored(monkeypatch, ids):
    _enumerating(monkeypatch, ids)
    assert device.candidates() == []


# -- saving -------------------------------------------------------------------


def test_eq_save_refuses_a_factory_slot(capsys):
    with pytest.raises(FiioError, match="slot 2 is a factory preset; save to one of 7, 8, 9"):
        _dry().set("eq-save", 2)
    assert "TX" not in "".join(capsys.readouterr())


def test_eq_save_accepts_a_user_slot():
    assert _dry().set("eq-save", 8) == 8


def test_eq_save_is_unguarded_where_the_user_slots_are_unknown():
    assert _dry(registry.DEVICES["FIIO KA17"]).set("eq-save", 2) == 2


def test_apply_with_save_refuses_a_factory_slot_before_writing_anything(capsys):
    prof = {"preamp": -3.0, "bands": [{"freq": 100, "gain": 1.0, "q": 1.0, "filter": "peak"}]}
    with pytest.raises(FiioError, match="factory preset"):
        cli._apply_profile(_dry(), prof, SimpleNamespace(slot="rock", save=True))
    assert "aa 0a" not in "".join(capsys.readouterr())  # the SET header


# -- applying -----------------------------------------------------------------


def test_write_profile_reports_what_the_wire_can_carry():
    bands = [{"freq": 100.4, "gain": 2.7524, "q": 0.5779, "filter": "peak"}]
    _, written = _dry().write_profile(-3.0, bands, 1)
    assert written == [{"index": 0, "freq": 100, "gain": 2.8, "q": 0.58, "filter": "peak"}]


# -- opening ------------------------------------------------------------------


def _unopenable(monkeypatch, name):
    def refuse(_path):
        raise OSError("open failed")

    info = {"vendor_id": 0x2972, "product_id": 0x0104, "product_string": name, "path": b"p"}
    monkeypatch.setattr(device, "hid", SimpleNamespace(
        enumerate=lambda: [info], device=lambda: SimpleNamespace(open_path=refuse)))


def test_an_unnamed_device_that_will_not_open_says_what_was_found(monkeypatch):
    _unopenable(monkeypatch, "")
    with pytest.raises(FiioError, match=r"found a HID device \(2972:0104\).*udev rule"):
        Dac.open()


def test_a_named_device_that_will_not_open_is_named(monkeypatch):
    _unopenable(monkeypatch, "FIIO KA15")
    with pytest.raises(FiioError, match="cannot open 'FIIO KA15'"):
        Dac.open()


@pytest.mark.parametrize("kwargs", [{"product": "ka15"}, {"serial": "ABC123"}])
def test_filters_do_not_hide_a_device_with_no_readable_name(monkeypatch, kwargs):
    _unopenable(monkeypatch, "")
    with pytest.raises(FiioError, match=r"found a HID device \(2972:0104\).*udev rule"):
        Dac.open(**kwargs)


def test_a_named_device_that_does_not_match_is_still_no_match(monkeypatch):
    _unopenable(monkeypatch, "FIIO KA17")
    with pytest.raises(FiioError, match="no FiiO device matched"):
        Dac.open(product="ka15")


def test_a_dry_run_with_save_still_shows_its_writes(capsys):
    prof = {"preamp": -3.0, "bands": [{"freq": 100, "gain": 1.0, "q": 1.0, "filter": "peak"}]}
    with pytest.raises(FiioError, match="cannot read"):
        cli._apply_profile(_dry(), prof, SimpleNamespace(slot=None, save=True))
    assert "".join(capsys.readouterr()).count("aa 0a") == 1 + 10  # pre-amp and every band


@pytest.mark.parametrize("slot, what", [(99, "does not exist"), (10, "is the EQ-off setting")])
def test_eq_save_says_what_a_refused_slot_actually_is(slot, what):
    with pytest.raises(FiioError, match=f"slot {slot} {what}.*save to one of 7, 8, 9"):
        _dry().set("eq-save", slot)


def test_slot_is_resolved_like_any_other_preset(capsys):
    prof = {"preamp": -3.0, "bands": [{"freq": 100, "gain": 1.0, "q": 1.0, "filter": "peak"}]}
    with pytest.raises(FiioError, match="not a number or a preset name"):
        cli._apply_profile(_dry(), prof, SimpleNamespace(slot="nonsense", save=False))
    with pytest.raises(FiioError, match="99 is not a slot on this device"):
        cli._apply_profile(_dry(), prof, SimpleNamespace(slot="99", save=False))
    assert "aa 0a" not in "".join(capsys.readouterr())
    cli._apply_profile(_dry(), prof, SimpleNamespace(slot="USER2", save=False))
    assert "aa 0a 00 00 16 01 08" in "".join(capsys.readouterr())


def test_a_band_and_a_profile_round_a_value_the_same_way():
    direct = _dry().set_band(0, 100, 0.15, 1.0, "peak")
    _, written = _dry().write_profile(-3.0, [{"freq": 100, "gain": 0.15, "q": 1.0,
                                              "filter": "peak"}], 1)
    assert direct["gain"] == written[0]["gain"] == 0.2


def test_write_profile_flattens_the_bands_it_does_not_use():
    _, written = _dry().write_profile(-3.0, [], 2)
    assert [(b["index"], b["freq"], b["gain"], b["q"]) for b in written] == [
        (0, 1000, 0.0, 0.7), (1, 1000, 0.0, 0.7)]
