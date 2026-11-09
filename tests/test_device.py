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
