"""USB HID transport for FiiO devices.

Thin wrapper over hidapi that adds the three things the official app does and
that matter for reliability: picking the vendor HID interface rather than the
audio-control one, pacing commands (the firmware drops back-to-back writes),
and correlating a reply with the command that asked for it.
"""

import contextlib
import importlib
import math
import os
import sys
import time

from . import proto, registry

try:  # Windows has no fcntl, and only Linux's hidraw needs it
    import fcntl
except ImportError:
    fcntl = None


def _backend():
    """The hidapi module to use, or None if hidapi is not installed."""
    # On Linux `hid` is the libusb backend, which detaches the kernel driver on
    # every open, cannot read a name without permission and reports no usage page.
    names = ("hidraw", "hid") if sys.platform == "linux" else ("hid",)
    for name in names:
        with contextlib.suppress(ImportError):
            return importlib.import_module(name)
    return None


hid = _backend()  # optional so `fiioctl schema` and `--help` work without hidapi


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
    except (TypeError, ValueError, OverflowError):
        raise FiioError(f"{name} must be a whole number; got {value!r}") from None
    if not math.isfinite(number) or number != int(number):
        raise FiioError(f"{name} must be a whole number; got {value!r}")
    return int(number)


def _clamp(value, limits):
    lo, hi = limits
    return max(lo, min(hi, value))


def _unnamed_error(info, detail=""):
    """Why a device with no readable name cannot be used."""
    # without permission, hidapi's libusb backend cannot read the name either,
    # so the model is unknown and no profile can be matched
    usb_id = f"{info.get('vendor_id', 0):04x}:{info.get('product_id', 0):04x}"
    return FiioError(
        f"found a HID device ({usb_id}) but could not read its name{detail}. On Linux "
        "this usually means the udev rule is not installed (see README); on macOS "
        "re-plug the device."
    )


def _open_hint(path):
    """What to try when a device will not open; a hidraw path can say which it is."""
    if not path.startswith(b"/dev/"):
        return ("Another program may be using it. On Linux, check the udev rule is "
                "installed (see README); on macOS re-plug the device.")
    node = path.decode()
    if not os.path.exists(path):
        return f"{node} has gone, so check the device is still connected."
    if not os.access(path, os.R_OK | os.W_OK):
        return (f"Your user has no access to {node}: install the udev rule (see "
                "README), re-plug the device, and check you are logged in at the machine.")
    return "Your user has access to it, so try re-plugging the device."


def _lock_for(path):
    """A descriptor for taking turns on a hidraw node, which many can have open."""
    if fcntl is None or not path.startswith(b"/dev/"):
        return None
    with contextlib.suppress(OSError):
        return os.open(path, os.O_RDONLY)
    return None


def _hid():
    if hid is None:
        raise FiioError("the 'hid' package is not installed: pip install hid")
    return hid


def candidates():
    """All HID interfaces that could be a FiiO control endpoint."""
    out = []
    for info in _hid().enumerate():
        # only 0x2972 is FiiO's own; the others are controller vendors whose
        # unrelated products must not be mistaken for a DAC
        products = registry.VENDORS.get(info.get("vendor_id"), ())
        if products is None or info.get("product_id") in products:
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
        self._lock = None

    # -- lifecycle ----------------------------------------------------------

    #: why a model cannot be driven, keyed by its declared transport
    UNSUPPORTED = {
        "adapter": ("FiiO's app handles this model through its own controller, "
                    "and fiioctl has not been tested on it"),
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
        found = infos
        if serial:
            infos = [i for i in infos if (i.get("serial_number") or "") == serial]
        if product:
            want = product.upper()
            infos = [i for i in infos if want in (i.get("product_string") or "").upper()]
        if not infos:
            # filtering cannot match a name or serial that could not be read
            unnamed = next((i for i in found if not i.get("product_string")), None)
            if unnamed:
                raise _unnamed_error(unnamed)
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
            if not info.get("product_string"):
                raise _unnamed_error(info, f" or open it: {exc}") from exc
            raise FiioError(
                f"cannot open {info.get('product_string')!r}: {exc}. "
                + _open_hint(info["path"])
            ) from exc
        handle.set_nonblocking(False)
        dac = cls(handle, info, spec, trace, dry)
        if not dry:
            dac._lock = _lock_for(info["path"])
        return dac

    def close(self):
        if self.handle is None:
            return
        with contextlib.suppress(Exception):  # closing should never be fatal
            self.handle.close()
        if self._lock is not None:
            with contextlib.suppress(OSError):
                os.close(self._lock)
            self._lock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- raw I/O ------------------------------------------------------------

    @staticmethod
    def _log(direction, data):
        # stderr, so a packet log cannot corrupt the JSON on stdout
        print(f"{direction} {data.hex(' ')}", file=sys.stderr)

    def _pace(self):
        wait = self.spec.interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def _write(self, packet):
        buf = bytes([self.spec.report_id]) + packet
        if self.dry:
            self._log("TX", buf)
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
                self._log("TX", out)
            return
        raise FiioError(f"write failed: {last}")

    @contextlib.contextmanager
    def _turn(self):
        """One exchange with the device, not overlapping any other program's."""
        if self._lock is None:
            yield
            return
        fcntl.flock(self._lock, fcntl.LOCK_EX)
        try:
            # every reader of a hidraw node gets every report, so anything
            # already waiting here was another program's reply
            with contextlib.suppress(OSError):
                while self.handle.read(65, timeout_ms=1):
                    pass
            yield
        finally:
            self._pace()  # whoever goes next may be another program
            fcntl.flock(self._lock, fcntl.LOCK_UN)

    def send(self, cmd, data=b"", hdr=proto.SET):
        """Fire a command without waiting for a reply."""
        with self._turn():
            self._write(proto.frame(hdr, cmd, data))

    def request(self, cmd, data=b"", hdr=proto.GET, timeout=None):
        """Send a command and return the payload of the matching reply."""
        with self._turn():
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
                    self._log("RX", bytes(report))
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
        if not math.isfinite(value):
            raise FiioError(f"gain must be a finite number; got {value}")
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

    def resolve_preset(self, value):
        """The slot number a preset name or number refers to on this device."""
        # preset names are per-device, so they live on the spec rather than in
        # Cmd.enum; resolve one before trying to read a number
        if isinstance(value, str) and value.strip().lower() in self.spec.presets:
            return self.spec.presets[value.strip().lower()]
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            valid = ", ".join(sorted(self.spec.presets))
            raise FiioError(f"eq-preset {value!r} is not a number or a preset "
                            f"name; names: {valid}") from None
        if number not in set(self.spec.presets.values()):
            valid = ", ".join(f"{k} ({v})" for k, v in
                              sorted(self.spec.presets.items(), key=lambda kv: kv[1]))
            raise FiioError(f"eq-preset {number} is not a slot on this device; "
                            f"valid: {valid}")
        return number

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
            except (TypeError, ValueError, OverflowError):
                number = None
            if number is None or number not in cmd.enum.values():
                options = ", ".join(f"{text} ({num})"
                                    for text, num in sorted(cmd.enum.items(),
                                                            key=lambda kv: kv[1]))
                raise FiioError(f"{name} accepts {options}; got {value!r}")
            return number

        if name == "eq-preset" and self.spec.presets:
            return self.resolve_preset(value)

        if name == "eq-gain":
            return self.snap_gain(value)

        if cmd.codec == "bool":
            try:
                return proto.parse_bool(value)
            except ValueError as exc:
                raise FiioError(f"{name}: {exc}") from None

        if cmd.codec in INTEGER_CODECS:
            value = _integral(name, value)

        limits = cmd.rng if cmd.rng is not None else CODEC_LIMITS.get(cmd.codec)
        if limits is not None:
            lo, hi = limits
            if not lo <= float(value) <= hi:
                raise FiioError(f"{name}={value} outside {lo}..{hi}")
        if name == "eq-save":
            self.require_user_slot(value)
        return value

    def require_user_slot(self, slot):
        """Refuse to save over a factory preset, on models whose user slots are known."""
        slots = self.spec.name_slots
        if not slots or slot in slots:
            return
        if slot == self.spec.eq_off:
            what = "is the EQ-off setting"
        elif slot in self.spec.presets.values():
            what = "is a factory preset"
        else:
            what = "does not exist on this device"
        raise FiioError(f"slot {slot} {what}; save to one of "
                        f"{', '.join(str(s) for s in slots)}")

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
        payload = proto.enc_band(index, freq, gain, q, filter_type)
        self.send(registry.BAND_CMD, payload)
        # as it went over the wire, not as it was typed
        return proto.dec_band(payload)

    def write_profile(self, preamp, bands, count):
        """Write a profile's pre-amp and bands, flattening any bands it leaves unused.

        Returns (pre-amp as stored, every band as written), which can differ from
        what the profile asked for once the device's limits are applied.
        """
        # round the pre-amp downward: the device truncates toward zero, and
        # keeping less attenuation than the profile asked for is what clips
        stored = self.snap_gain(preamp, safe=True)
        self.set("eq-gain", stored)
        chosen = [(b["freq"], _clamp(float(b["gain"]), self.spec.gain_range),
                   _clamp(float(b["q"]), self.spec.q_range), b["filter"]) for b in bands[:count]]
        chosen += [(1000, 0.0, 0.7, "peak")] * (count - len(chosen))
        written = [self.set_band(i, *band) for i, band in enumerate(chosen)]
        return stored, written

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
