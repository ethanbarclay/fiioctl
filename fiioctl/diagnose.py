"""A read-only report on every FiiO HID interface, for when a device will not talk.

It collects what a bug report needs in one go: the HID report descriptor, the USB
interfaces and endpoints, file access, and how the device answers a few GET
commands at every write length and report id worth trying. Nothing it sends can
change a setting: every frame is a GET, for a fixed list of commands.
"""

import contextlib
import glob
import os
import platform
import sys
import time
from importlib import metadata

from . import __version__, proto, registry
from .device import Dac, FiioError, _hid, candidates

#: GETs to probe with, chosen because FiiO's app sends every one of them to the
#: adapter models (the FG3 among them) when it connects
PROBE_CMDS = ("eq-count", "eq-switch", "eq-preset", "firmware", "volume")

#: how long to collect reports after each probe
LISTEN_S = 0.4

ITEM_MAIN = {0x8: "input", 0x9: "output", 0xB: "feature"}


def parse_descriptor(rd):
    """Sizes in bytes of each report a HID report descriptor declares.

    Returns {"usage_pages": [...], "reports": [{"id", "kind", "bytes"}, ...]}, the
    usage pages being those of the top-level collections. Only the items that
    decide a report's size are followed, which is all a length question needs.
    """
    rd = bytes(rd)
    state = {"page": 0, "size": 0, "count": 0, "id": 0}
    stack, bits, pages, depth, i = [], {}, [], 0, 0
    while i < len(rd):
        prefix = rd[i]
        if prefix == 0xFE:  # long item: skip it whole
            i += 3 + (rd[i + 1] if i + 1 < len(rd) else 0)
            continue
        size = (0, 1, 2, 4)[prefix & 3]
        value = int.from_bytes(rd[i + 1:i + 1 + size], "little")
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        i += 1 + size
        if kind == 0:  # main
            if tag in ITEM_MAIN:
                key = (state["id"], ITEM_MAIN[tag])
                bits[key] = bits.get(key, 0) + state["size"] * state["count"]
            elif tag == 0xA:  # collection
                if depth == 0:
                    pages.append(state["page"])
                depth += 1
            elif tag == 0xC:  # end collection
                depth = max(0, depth - 1)
        elif kind == 1:  # global
            if tag == 0x0:
                state["page"] = value
            elif tag == 0x7:
                state["size"] = value
            elif tag == 0x9:
                state["count"] = value
            elif tag == 0x8:
                state["id"] = value
            elif tag == 0xA:
                stack.append(dict(state))
            elif tag == 0xB and stack:
                state = stack.pop()
    reports = [{"id": rid, "kind": k, "bytes": (n + 7) // 8}
               for (rid, k), n in sorted(bits.items())]
    return {"usage_pages": [hex(p) for p in pages], "reports": reports}


def _read(path):
    with contextlib.suppress(OSError), open(path, "rb") as fh:
        return fh.read()
    return None


def _text(path):
    raw = _read(path)
    return raw.decode(errors="replace").strip() if raw is not None else None


def _sysfs(node):
    """The report descriptor and USB layout behind a hidraw node, from sysfs."""
    name = os.path.basename(node)
    hid_dir = os.path.realpath(f"/sys/class/hidraw/{name}/device")
    if not os.path.isdir(hid_dir):
        return {}
    out = {}
    rd = _read(os.path.join(hid_dir, "report_descriptor"))
    if rd is not None:
        out["report_descriptor"] = rd.hex(" ")
    usb_dev = os.path.dirname(os.path.dirname(hid_dir))  # hid -> interface -> device
    attrs = ("idVendor", "idProduct", "bcdDevice", "manufacturer", "product", "speed",
             "bConfigurationValue")
    out["usb_device"] = {a: _text(os.path.join(usb_dev, a)) for a in attrs}
    interfaces = []
    for intf in sorted(glob.glob(os.path.join(usb_dev, os.path.basename(usb_dev) + ":*"))):
        row = {a: _text(os.path.join(intf, a)) for a in (
            "bInterfaceNumber", "bAlternateSetting", "bInterfaceClass",
            "bInterfaceSubClass", "bInterfaceProtocol", "bNumEndpoints")}
        driver = os.path.join(intf, "driver")
        row["driver"] = (os.path.basename(os.path.realpath(driver))
                         if os.path.exists(driver) else None)
        row["endpoints"] = [
            {a: _text(os.path.join(ep, a)) for a in ("bEndpointAddress", "type", "wMaxPacketSize")}
            for ep in sorted(glob.glob(os.path.join(intf, "ep_*")))
        ]
        interfaces.append(row)
    out["usb_interfaces"] = interfaces
    return out


def _listen(handle, until):
    """Every report that arrives before `until`, as hex."""
    got = []
    while time.monotonic() < until:
        with contextlib.suppress(OSError):
            report = handle.read(65, timeout_ms=50)
            if report:
                got.append(bytes(report).hex(" "))
    return got


def probe(handle, report_ids, lengths=Dac.WRITE_LENS, listen=LISTEN_S, pause=0.12):
    """Send each probe GET at every report id and length; record what comes back."""
    rows = []
    for name in PROBE_CMDS:
        cmd = registry.COMMANDS[name].id
        if cmd in registry.DESTRUCTIVE:  # none are, but a renumbering must not slip one in
            continue
        packet = proto.frame(proto.GET, cmd)
        for rid in report_ids:
            for n in lengths:
                _listen(handle, time.monotonic() + 0.02)  # drop anything stale
                out = bytes([rid]) + packet
                out = out.ljust(n, b"\0") if n else out
                try:
                    wrote = handle.write(out)
                except (OSError, ValueError) as exc:
                    wrote = f"error: {exc}"
                replies = _listen(handle, time.monotonic() + listen)
                answered = any((proto.parse(bytes.fromhex(r)) or (None,))[0] == cmd
                               for r in replies)
                rows.append({"cmd": cmd, "name": name, "report_id": rid, "length": len(out),
                             "wrote": wrote, "answered": answered, "reports": replies})
                time.sleep(pause)
    return rows


def _feature_reads(handle, ids):
    """GET_REPORT for each declared feature report; a read, so it changes nothing."""
    out = []
    for rid in ids:
        try:
            data = handle.get_feature_report(rid, 65)
            out.append({"report_id": rid, "data": bytes(data).hex(" ")})
        except (OSError, ValueError, AttributeError) as exc:
            out.append({"report_id": rid, "error": str(exc)})
    return out


def _interface(info, run_probe):
    path = info["path"]
    node = path.decode(errors="replace")
    row = {
        "path": node,
        "product": info.get("product_string"),
        "matched_profile": registry.match(info.get("product_string")).name,
        "usb_id": f"{info.get('vendor_id', 0):04x}:{info.get('product_id', 0):04x}",
        "interface": info.get("interface_number"),
        "usage_page": hex(info.get("usage_page") or 0),
        "usage": hex(info.get("usage") or 0),
    }
    if node.startswith("/dev/"):
        row["access"] = {"exists": os.path.exists(path),
                         "read_write": os.access(path, os.R_OK | os.W_OK)}
        row.update(_sysfs(node))
    handle = _hid().device()
    try:
        handle.open_path(path)
    except OSError as exc:
        row["open_error"] = str(exc)
        return row
    try:
        handle.set_nonblocking(False)
        if "report_descriptor" not in row:
            with contextlib.suppress(Exception):  # only newer hidapi builds have it
                row["report_descriptor"] = bytes(handle.get_report_descriptor()).hex(" ")
        layout = parse_descriptor(bytes.fromhex(row["report_descriptor"])) \
            if row.get("report_descriptor") else {"usage_pages": [], "reports": []}
        row["descriptor"] = layout
        outputs = sorted({r["id"] for r in layout["reports"] if r["kind"] == "output"})
        spec = registry.match(info.get("product_string"))
        ids = [spec.report_id] + [i for i in outputs if i != spec.report_id]
        features = sorted({r["id"] for r in layout["reports"] if r["kind"] == "feature"})
        if features:
            row["feature_reports"] = _feature_reads(handle, features)
        if run_probe:
            row["probe"] = probe(handle, ids)
    finally:
        with contextlib.suppress(Exception):
            handle.close()
    return row


def report(run_probe=True):
    """The whole diagnosis as one JSON-ready dict."""
    env = {
        "fiioctl": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "backend": None,
        "hidapi": None,
    }
    with contextlib.suppress(metadata.PackageNotFoundError):
        env["hidapi"] = metadata.version("hidapi")
    try:
        env["backend"] = _hid().__name__
        infos = candidates()
    except FiioError as exc:
        return {"environment": env, "error": str(exc), "interfaces": []}
    return {"environment": env, "interfaces": [_interface(i, run_probe) for i in infos]}


def summary(result):
    """A few lines a person can read before scrolling into the JSON."""
    lines = [f"fiioctl {result['environment']['fiioctl']}, backend "
             f"{result['environment']['backend']}, {result['environment']['platform']}"]
    if result.get("error"):
        return lines + [f"error: {result['error']}"]
    if not result["interfaces"]:
        return lines + ["no FiiO HID interfaces found"]
    for row in result["interfaces"]:
        lines.append(f"{row['path']}: {row['product']!r} iface={row['interface']} "
                     f"usage_page={row['usage_page']}")
        if row.get("open_error"):
            lines.append(f"  could not open: {row['open_error']}")
            continue
        sizes = ", ".join(f"{r['kind']} id {r['id']} = {r['bytes']} bytes"
                          for r in row.get("descriptor", {}).get("reports", []))
        lines.append(f"  reports: {sizes or 'descriptor not available'}")
        probes = row.get("probe")
        if probes is None:
            continue
        worked = sorted({(p["report_id"], p["length"]) for p in probes if p["answered"]})
        if worked:
            lines.append("  answered at: " + ", ".join(
                f"report id {rid} length {n}" for rid, n in worked))
        else:
            lines.append("  no probe got an answer at any report id or length")
        silent = sorted({p["name"] for p in probes} - {p["name"] for p in probes if p["answered"]})
        if worked and silent:
            lines.append("  never answered: " + ", ".join(silent))
    return lines
