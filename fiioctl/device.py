"""USB HID transport for FiiO devices.

Thin wrapper over hidapi that adds the three things the official app does and
that matter for reliability: picking the vendor HID interface rather than the
audio-control one, pacing commands (the firmware drops back-to-back writes),
and correlating a reply with the command that asked for it.
"""

import contextlib
import math
import time

from . import proto, registry

try:  # kept optional so `fiioctl schema` and `--help` work without hidapi present
    import hid
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    hid = None


class FiioError(RuntimeError):
    """Any failure talking to the device."""


#: codecs that encode a whole number. A fractional value would reach `int()`
#: inside the encoder and raise a bare ValueError, or silently truncate.
INTEGER_CODECS = ("u8", "u16", "balance")

#: what an integer codec can carry. The encoders mask to the field width, so
#: without this a command with no declared range would wrap: volume -1 goes out
#: as 0xFF, the loudest value there is.
CODEC_LIMITS = {"u8": (0, 0xFF), "u16": (0, 0xFFFF)}


def _integral(name, value):
    """Require a whole number, so bad input fails as a FiioError not a traceback."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FiioError(f"{name} must be a whole number; got {value!r}") from None
    if not math.isfinite(number) or number != int(number):
        raise FiioError(f"{name} must be a whole number; got {value!r}")
    return int(number)


def _hid():
    if hid is None:
        raise FiioError("the 'hid' package is not installed: pip install hid")
    return hid


def candidates():
    """All HID interfaces that could be a FiiO control endpoint."""
    out = []
    for info in _hid().enumerate():
        if info.get("vendor_id") in registry.VENDORS:
            out.append(info)
    return out


def _pick(infos):
    """Prefer a vendor-defined usage page; that is the control interface."""
    vendor = [i for i in infos if (i.get("usage_page") or 0) >= 0xFF00]
    return (vendor or infos)[-1]


class Dac:
    """An open connection to one device."""

    #: write lengths to try, in order; the working one is cached per connection
    WRITE_LENS = (64, 65, 33, 0)

    def __init__(self, handle, info, spec, trace=False, dry=False):
        self.handle = handle
        self.info = info
        self.spec = spec
        self.trace = trace
        self.dry = dry
        self._wlen = None
        self._last = 0.0

    # -- lifecycle ----------------------------------------------------------

    #: why a model cannot be driven, keyed by its declared transport
    UNSUPPORTED = {
        "adapter": ("the official app drives this model through a different "
                    "protocol, which fiioctl does not implement"),
        "serial": ("this model is driven over USB CDC serial rather than HID, "
                   "and fiioctl has no serial transport"),
    }

    @classmethod
    def open(cls, serial=None, product=None, trace=False, dry=False, force=False):
        try:
            infos = candidates()
        except FiioError:
            if not dry:
                raise
            infos = []
        if not infos:
            if dry:  # let --dry-run work with nothing plugged in
                name = product or "FIIO KA15"
                return cls(None, {"product_string": name}, registry.match(name), trace, True)
            # A serial-only model never shows up in an HID enumeration, so the
            # registry's transport flag cannot explain it later - say it here.
            serial = registry.models_using("serial")
            hint = (f" Note that some models are not HID devices and will never "
                    f"appear here: {', '.join(serial)} "
                    f"{'is' if len(serial) == 1 else 'are'} driven over USB CDC "
                    f"serial, which fiioctl does not implement.") if serial else ""
            raise FiioError(
                "no FiiO device found over HID (check the cable, and udev rules "
                "on Linux)." + hint)
        if serial:
            infos = [i for i in infos if (i.get("serial_number") or "") == serial]
        if product:
            want = product.upper()
            infos = [i for i in infos if want in (i.get("product_string") or "").upper()]
        if not infos:
            raise FiioError(f"no FiiO device matched serial={serial!r} product={product!r}")
        info = _pick(infos)
        spec = registry.match(info.get("product_string"))
        reason = cls.UNSUPPORTED.get(spec.transport)
        if reason and not force and not dry:
            raise FiioError(
                f"{spec.name}: {reason}. See docs/PROTOCOL.md. "
                "Pass --force to talk to it anyway as if it were an HID device."
            )
        handle = _hid().device()
        try:
            handle.open_path(info["path"])
        except OSError as exc:
            if dry:
                return cls(None, info, spec, trace, True)
            raise FiioError(
                f"cannot open {info.get('product_string')!r}: {exc}. "
                "On Linux install the udev rule (see README); on macOS re-plug the device."
            ) from exc
        handle.set_nonblocking(False)
        return cls(handle, info, spec, trace, dry)

    def close(self):
        if self.handle is None:
            return
        with contextlib.suppress(Exception):  # closing should never be fatal
            self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- raw I/O ------------------------------------------------------------

    def _pace(self):
        wait = self.spec.interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def _write(self, packet):
        buf = bytes([self.spec.report_id]) + packet
        if self.dry:
            print(f"TX {buf.hex(' ')}")
            return
        self._pace()
        lens = (self._wlen,) if self._wlen else self.WRITE_LENS
        last = None
        for n in lens:
            out = buf.ljust(n, b"\x00") if n else buf
            try:
                written = self.handle.write(out)
            except (OSError, ValueError) as exc:
                last = exc
                continue
            # hidapi reports a failed write by returning -1, not by raising
            if written < 0:
                last = f"hid_write returned {written}"
                continue
            self._wlen = n
            self._last = time.monotonic()
            if self.trace:
                print(f"TX {out.hex(' ')}")
            return
        raise FiioError(f"write failed: {last}")

    def send(self, cmd, data=b"", hdr=proto.SET):
        """Fire a command without waiting for a reply."""
        self._write(proto.frame(hdr, cmd, data))

    def request(self, cmd, data=b"", hdr=proto.GET, timeout=None):
        """Send a command and return the payload of the matching reply."""
        self._write(proto.frame(hdr, cmd, data))
        if self.dry:
            raise FiioError("--dry-run cannot read values back from the device")
        deadline = time.monotonic() + (timeout or self.spec.timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise FiioError(f"timed out waiting for reply to command {cmd}")
            try:
                report = self.handle.read(65, timeout_ms=max(1, int(left * 1000)))
            except OSError as exc:
                raise FiioError(f"read failed: {exc}") from exc
            if not report:
                continue
            if self.trace:
                print(f"RX {bytes(report).hex(' ')}")
            parsed = proto.parse(report)
            if parsed and parsed[0] == cmd:
                return parsed[1]

    # -- typed access -------------------------------------------------------

    def snap_gain(self, value, safe=False):
        """Quantise an EQ global-gain value to what the device can actually store.

        The firmware truncates fractions toward zero, so -6.6 would land as -6.0
        - less attenuation than asked for. Rounding here makes the value we
        report match the value the device keeps.

        `safe=True` rounds downward instead of to nearest, for pre-amps taken
        from a profile: losing attenuation can clip, gaining a little cannot.
        """
        step = self.spec.gain_step or 0.1
        value = float(value)
        snapped = math.floor(value / step) * step if safe else round(value / step) * step
        lo, hi = self.spec.gain_range
        return round(max(lo, min(hi, snapped)), 1)

    @staticmethod
    def _cmd(name):
        try:
            return registry.COMMANDS[name]
        except KeyError:
            raise FiioError(f"unknown setting {name!r}; "
                            "run 'fiioctl schema' for the list") from None

    def get(self, name, timeout=None):
        cmd = self._cmd(name)
        if not cmd.readable:
            raise FiioError(f"{name} is write-only")
        decode = proto.CODECS[cmd.codec][1]
        return decode(self.request(cmd.id, timeout=timeout))

    def _coerce(self, name, cmd, value):
        """Resolve a value to something the device will actually accept.

        The firmware does not report errors: an out-of-range write is either
        ignored, leaving the old value, or clamped to a different one. Either way
        a caller that trusted the request would be told of a change that did not
        happen. So reject what we know to be invalid before sending it.
        """
        if cmd.enum:
            if str(value) in cmd.enum:
                return cmd.enum[str(value)]
            try:
                number = int(value)
            except (TypeError, ValueError):
                number = None
            if number is None or number not in cmd.enum.values():
                options = ", ".join(f"{text} ({num})"
                                    for text, num in sorted(cmd.enum.items(),
                                                            key=lambda kv: kv[1]))
                raise FiioError(f"{name} accepts {options}; got {value!r}")
            return number

        if name == "eq-preset" and self.spec.presets:
            # preset names are per-device, so they live on the spec rather than
            # in Cmd.enum; resolve one before trying to read a number
            if isinstance(value, str) and value.strip().lower() in self.spec.presets:
                return self.spec.presets[value.strip().lower()]
            try:
                number = int(value)
            except (TypeError, ValueError):
                valid = ", ".join(sorted(self.spec.presets))
                raise FiioError(f"eq-preset {value!r} is not a number or a preset "
                                f"name; names: {valid}") from None
            if number not in set(self.spec.presets.values()):
                valid = ", ".join(f"{k} ({v})" for k, v in
                                  sorted(self.spec.presets.items(), key=lambda kv: kv[1]))
                raise FiioError(f"eq-preset {number} is not a slot on this device; "
                                f"valid: {valid}")
            return number

        if name == "eq-gain":
            return self.snap_gain(value)

        if cmd.codec in INTEGER_CODECS:
            value = _integral(name, value)

        limits = cmd.rng if cmd.rng is not None else CODEC_LIMITS.get(cmd.codec)
        if limits is not None:
            lo, hi = limits
            if not lo <= float(value) <= hi:
                raise FiioError(f"{name}={value} outside {lo}..{hi}")
        return value

    def set(self, name, value, verify=False):
        """Write a setting. With `verify`, read it back and return what stuck.

        Verification costs one extra round-trip, so it is off by default for
        interactive callers that re-read anyway, and on for one-shot CLI writes.
        """
        cmd = self._cmd(name)
        if not cmd.writable:
            raise FiioError(f"{name} is read-only")
        encode = proto.CODECS[cmd.codec][0]
        if encode is None:
            raise FiioError(f"{name} is read-only")

        value = self._coerce(name, cmd, value)
        payload = encode(value)
        self.send(cmd.id, payload)
        # Report the value as it went over the wire, not as it was typed.
        sent = proto.CODECS[cmd.codec][1](payload)
        # a dry run has nothing to read back from, and verifying would turn a
        # command that only builds packets into an error
        if not verify or not cmd.readable or self.dry:
            return sent
        actual = self.get(name)
        if actual != sent:
            raise FiioError(f"{name}: wrote {sent} but the device holds {actual}")
        return actual

    # -- PEQ ----------------------------------------------------------------

    def band_count(self):
        if self.spec.bands:
            return self.spec.bands
        try:
            return self.get("eq-count")
        except FiioError:
            return 10

    def get_band(self, index):
        return proto.dec_band(self.request(registry.BAND_CMD, bytes([index])))

    def set_band(self, index, freq, gain, q, filter_type=0):
        index = _integral("band index", index)
        # the band count can need a round-trip, so an unknown one is only held
        # to what the index byte can carry
        top = (self.spec.bands or 0x100) - 1
        if not 0 <= index <= top:
            raise FiioError(f"band index {index} outside 0..{top}")
        if not 1 <= float(freq) <= 0xFFFF:
            raise FiioError(f"band frequency {freq} outside 1..65535 Hz")
        lo, hi = self.spec.gain_range
        if not lo <= float(gain) <= hi:
            raise FiioError(f"band gain {gain} outside {lo}..{hi} dB")
        lo, hi = self.spec.q_range
        if not lo <= float(q) <= hi:
            raise FiioError(f"band Q {q} outside {lo}..{hi}")
        self.send(registry.BAND_CMD, proto.enc_band(index, freq, gain, q, filter_type))

    def scan(self, limit=256, timeout=0.25):
        """Probe the command space read-only; return {cmd: payload}.

        Sends only GET frames, and never touches `registry.DESTRUCTIVE`. Useful
        for finding settings a device answers for that are not yet decoded - diff
        two scans either side of changing one setting in the vendor app and the
        command that moved is that setting.
        """
        found = {}
        for cmd in range(int(limit)):
            if cmd in registry.DESTRUCTIVE:
                continue
            try:
                found[cmd] = self.request(cmd, timeout=timeout)
            except FiioError:
                continue
        return found

    def get_preset_name(self, slot):
        payload = self.request(registry.NAME_CMD, bytes([slot - self.spec.name_offset]))
        return proto.dec_name(payload[1:] if payload else b"")

    def set_preset_name(self, slot, text):
        if slot not in self.spec.name_slots:
            raise FiioError(f"slot {slot} is not renameable on this device")
        clean = "".join(c for c in text if c.isalnum())[: self.spec.name_max]
        if not clean:
            raise FiioError("preset name must contain at least one alphanumeric character")
        # pad to the full field width: the firmware leaves whatever was there
        # before, so a short name would read back with trailing garbage
        self.send(registry.NAME_CMD,
                  proto.enc_name(slot - self.spec.name_offset, clean, self.spec.name_max))
        return clean
