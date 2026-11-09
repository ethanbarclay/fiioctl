"""FiiO HID wire protocol: frame construction, parsing, and value codecs.

Frame layout, identical for every FiiO device seen in the official web app:

    [hdr0, hdr1, 0x00, 0x00, cmd, len, *data, 0x00, 0xEE]

`hdr` is (0xAA, 0x0A) to write a value and (0xBB, 0x0B) to read one; device
replies also carry the (0xBB, 0x0B) header. Multi-byte values are big-endian.
"""

SET = (0xAA, 0x0A)
GET = (0xBB, 0x0B)
TERM = 0xEE
REPLY = bytes(GET)


def frame(hdr, cmd, data=b""):
    """Build a request frame."""
    data = bytes(data)
    return bytes([hdr[0], hdr[1], 0x00, 0x00, cmd, len(data), *data, 0x00, TERM])


def parse(buf):
    """Extract (cmd, payload) from a device report, or None if it isn't a reply.

    Tolerates a leading report-ID byte, which some hidapi backends prepend.
    """
    b = bytes(buf)
    i = b.find(REPLY)
    if i < 0 or len(b) < i + 7:
        return None
    cmd, n = b[i + 4], b[i + 5]
    return cmd, b[i + 6 : i + 6 + n]


# --- scalar codecs -----------------------------------------------------------


def _u16(v):
    return (int(v) & 0xFFFF).to_bytes(2, "big")


def _s16(b):
    v = int.from_bytes(b[:2], "big")
    return v - 0x10000 if v & 0x8000 else v


def _enc_balance(v):
    """Channel balance, byte-identical to the web app: >0 attenuates right."""
    v = int(v)
    if v > 0:
        return bytes([0, v & 0xFF])
    if v < 0:
        return bytes([v & 0xFF, 0])
    return b"\x00\x00"


def _dec_balance(b):
    left, right = b[0], b[1] if len(b) > 1 else 0
    if right:
        return right
    return -(left if left < 128 else 256 - left)


def _dec_version(b):
    if b and all(32 <= c < 127 for c in b.rstrip(b"\x00")):
        return b.rstrip(b"\x00").decode("ascii")
    return ".".join(str(c) for c in b) or None


def parse_bool(v):
    """Read a user's on/off value, refusing anything that is not clearly one."""
    word = str(v).strip().lower()
    if word in ("1", "on", "true", "yes", "y", "enable", "enabled"):
        return True
    if word in ("0", "off", "false", "no", "n", "disable", "disabled"):
        return False
    raise ValueError(f"expected on or off; got {v!r}")


def _enc_bool(v):
    return bytes([1 if parse_bool(v) else 0])


#: name -> (encode(value) -> bytes, decode(bytes) -> value). `None` encoder means
#: the command is read-only.
CODECS = {
    "u8": (lambda v: bytes([int(v) & 0xFF]), lambda b: b[0]),
    "u16": (_u16, lambda b: int.from_bytes(b[:2], "big")),
    "x10": (lambda v: _u16(round(float(v) * 10)), lambda b: _s16(b) / 10),
    "x100": (lambda v: _u16(round(float(v) * 100)), lambda b: int.from_bytes(b[:2], "big") / 100),
    "bool": (_enc_bool, lambda b: bool(b[0])),
    "balance": (_enc_balance, _dec_balance),
    "text": (lambda v: str(v).encode("utf-8"),
             lambda b: b.rstrip(b"\x00").decode("utf-8", "replace")),
    "version": (None, _dec_version),
    "raw": (bytes, lambda b: b.hex()),
}

#: Biquad filter types (web app enum order).
FILTERS = {
    "peak": 0,
    "low-shelf": 1,
    "high-shelf": 2,
    "band-pass": 3,
    "low-pass": 4,
    "high-pass": 5,
    "all-pass": 6,
}
FILTER_NAMES = {v: k for k, v in FILTERS.items()}


def enc_band(index, freq, gain, q, filter_type=0):
    """PEQ band payload: [index, gain*10, freq, q*100, type] (2-byte fields BE)."""
    if isinstance(filter_type, str):
        filter_type = FILTERS[filter_type]
    return bytes(
        [
            int(index) & 0xFF,
            *_u16(round(float(gain) * 10)),
            *_u16(int(round(float(freq)))),
            *_u16(round(float(q) * 100)),
            int(filter_type) & 0xFF,
        ]
    )


def dec_name(raw):
    """Decode a preset name from the device's fixed-width name field.

    The field is 8 bytes and the firmware does not clear it, so anything after
    the name is uninitialised memory - often recognisable fragments of an earlier
    PEQ write. Stop at the first byte that is not printable ASCII rather than
    only stripping trailing NULs, which would let embedded junk through.
    """
    out = bytearray()
    for byte in bytes(raw):
        if byte < 0x20 or byte > 0x7E:
            break
        out.append(byte)
    return out.decode("ascii", "replace").strip()


def enc_name(slot, text, width):
    """Build a preset-name payload, padding the field so no stale bytes remain."""
    return bytes([slot & 0xFF]) + text.encode("utf-8")[:width].ljust(width, b"\x00")


def dec_band(payload):
    """Inverse of :func:`enc_band`."""
    if len(payload) < 8:
        return None
    t = payload[7]
    return {
        "index": payload[0],
        "gain": _s16(payload[1:3]) / 10,
        "freq": int.from_bytes(payload[3:5], "big"),
        "q": int.from_bytes(payload[5:7], "big") / 100,
        "filter": FILTER_NAMES.get(t, t),
    }
