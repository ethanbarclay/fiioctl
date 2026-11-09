"""Device layer: write failures, input validation, device selection. No hardware."""

import pytest

from fiioctl import registry
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
