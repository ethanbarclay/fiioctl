"""Command line interface.

Subcommands for scalar settings are generated from :data:`registry.COMMANDS`,
so teaching fiioctl a newly decoded command is a one-line edit in the registry.
Every subcommand accepts ``--json`` and returns a stable exit code, which is
what makes the tool scriptable and agent-readable.
"""

import argparse
import json
import os
import sys

from . import eqdb, proto, registry
from .device import Dac, FiioError, candidates

EXIT_OK, EXIT_USAGE, EXIT_DEVICE, EXIT_DATA = 0, 2, 3, 4


def emit(obj, as_json, human=None):
    if as_json:
        json.dump(obj, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    elif human is not None:
        print(human)
    elif isinstance(obj, dict):
        width = max((len(k) for k in obj), default=0)
        for key, value in obj.items():
            print(f"{key.ljust(width)}  {value}")
    elif isinstance(obj, list):
        for row in obj:
            print(row)
    else:
        print(obj)


def shown(name, value):
    """Render a value for people: `english`, not `1`."""
    text = registry.label(name, value)
    return text if text is not None else value


def connect(args):
    return Dac.open(serial=args.serial, product=args.product, trace=args.trace, dry=args.dry_run)


# -- individual commands ------------------------------------------------------


def cmd_devices(args):
    rows = [
        {
            "product": i.get("product_string"),
            "serial": i.get("serial_number"),
            "vendor_id": hex(i.get("vendor_id", 0)),
            "product_id": hex(i.get("product_id", 0)),
            "usage_page": hex(i.get("usage_page") or 0),
            "interface": i.get("interface_number"),
        }
        for i in candidates()
    ]
    if not rows:
        emit([], args.json, human="no FiiO devices found")
        return EXIT_DEVICE
    emit(rows, args.json, None if args.json else "\n".join(
        f"{r['product']}  serial={r['serial']}  {r['vendor_id']}:{r['product_id']} "
        f"usage={r['usage_page']} iface={r['interface']}" for r in rows))
    return EXIT_OK


def cmd_info(args):
    with connect(args) as dac:
        spec = dac.spec
        out = {
            "product": dac.info.get("product_string"),
            "serial": dac.info.get("serial_number"),
            "matched_profile": spec.name,
            "report_id": spec.report_id,
            "min_interval_s": spec.interval,
            "gain_range_db": list(spec.gain_range),
            "capabilities": list(spec.caps),
            "presets": spec.presets,
            "renameable_slots": list(spec.name_slots),
        }
        if not args.dry_run:
            try:
                out["firmware"] = dac.get("firmware")
                out["bands"] = dac.band_count()
            except FiioError as exc:
                out["warning"] = str(exc)
        emit(out, args.json)
    return EXIT_OK


def cmd_state(args):
    with connect(args) as dac:
        caps = set(dac.spec.caps)
        out = {}
        for name, cmd in registry.COMMANDS.items():
            if not cmd.readable:
                continue
            need = registry.CAPS.get(name)
            if not args.all and (name in dac.spec.unsupported or (need and need not in caps)):
                continue
            try:
                # short timeout: an unsupported command simply never answers
                out[name] = dac.get(name, timeout=0.4 if not args.all else None)
            except FiioError:
                out[name] = None
        if args.json:
            emit(out, True)
        else:
            emit({k: shown(k, v) for k, v in out.items()}, False)
    return EXIT_OK


def cmd_get(args):
    with connect(args) as dac:
        value = dac.get(args.name)
    out = {args.name: value}
    text = registry.label(args.name, value)
    if text is not None:
        out["label"] = text
    emit(out, args.json, None if args.json else str(shown(args.name, value)))
    return EXIT_OK


def cmd_set(args):
    with connect(args) as dac:
        value = dac.set(args.name, args.value)
    out = {args.name: value, "written": True}
    text = registry.label(args.name, value)
    if text is not None:
        out["label"] = text
    emit(out, args.json, f"{args.name} = {shown(args.name, value)}")
    return EXIT_OK


def cmd_eq_preset(args):
    with connect(args) as dac:
        if args.value is None:
            emit({"eq-preset": dac.get("eq-preset")}, args.json)
            return EXIT_OK
        value = dac.spec.presets.get(str(args.value).lower(), args.value)
        dac.set("eq-preset", value)
        emit({"eq-preset": int(value), "written": True}, args.json, f"eq-preset = {value}")
    return EXIT_OK


def cmd_eq_name(args):
    with connect(args) as dac:
        if args.name_value is None:
            emit({"slot": args.slot, "name": dac.get_preset_name(args.slot)}, args.json)
        else:
            clean = dac.set_preset_name(args.slot, args.name_value)
            emit({"slot": args.slot, "name": clean, "written": True}, args.json,
                 f"slot {args.slot} = {clean}")
    return EXIT_OK


def cmd_eq_save(args):
    with connect(args) as dac:
        slot = args.slot if args.slot is not None else dac.get("eq-preset")
        dac.set("eq-save", slot)
        emit({"saved_to": slot}, args.json, f"saved EQ to slot {slot}")
    return EXIT_OK


def cmd_peq_show(args):
    with connect(args) as dac:
        count = dac.band_count()
        bands = []
        for i in range(count):
            try:
                bands.append(dac.get_band(i))
            except FiioError as exc:
                bands.append({"index": i, "error": str(exc)})
        out = {"bands": bands, "global_gain": dac.get("eq-gain"), "count": count}
        if args.json:
            emit(out, True)
        else:
            print(f"global gain  {out['global_gain']:+.1f} dB")
            for b in bands:
                if "error" in b:
                    print(f"  {b['index']:>2}  <{b['error']}>")
                else:
                    print(f"  {b['index']:>2}  {b['freq']:>6} Hz  {b['gain']:+6.1f} dB  "
                          f"Q {b['q']:>5.2f}  {b['filter']}")
    return EXIT_OK


def cmd_peq_set(args):
    with connect(args) as dac:
        dac.set_band(args.band, args.freq, args.gain, args.q, args.filter)
        freq = int(round(args.freq))
        emit({"index": args.band, "freq": freq, "gain": args.gain, "q": args.q,
              "filter": args.filter, "written": True}, args.json,
             f"band {args.band}: {freq} Hz {args.gain:+} dB Q {args.q} {args.filter}")
    return EXIT_OK


def _apply_profile(dac, prof, args):
    """Write a parsed ParametricEQ profile onto the device's bands."""
    count = dac.band_count()
    bands = prof["bands"][:count]
    dropped = prof["bands"][count:]
    if args.slot is not None:
        slot = dac.spec.presets.get(str(args.slot).lower(), args.slot)
        dac.set("eq-preset", int(slot))
    lo, hi = dac.spec.gain_range
    # round the pre-amp downward: the device truncates toward zero, and keeping
    # less attenuation than the profile asked for is what clips
    preamp = dac.snap_gain(prof["preamp"], safe=True)
    dac.set("eq-gain", preamp)
    for i, b in enumerate(bands):
        dac.set_band(i, b["freq"], max(lo, min(hi, b["gain"])), b["q"], b["filter"])
    for i in range(len(bands), count):
        dac.set_band(i, 1000, 0.0, 0.7, "peak")  # flatten unused bands
    if args.save:
        dac.set("eq-save", dac.get("eq-preset") if args.slot is None else int(slot))
    return {
        "applied": len(bands),
        "flattened": count - len(bands),
        "dropped": [f"{b['freq']}Hz" for b in dropped],
        "preamp": preamp,
        "preamp_clamped": preamp != prof["preamp"],
        "saved": bool(args.save),
    }


def cmd_eq_search(args):
    try:
        results = eqdb.search(args.query, limit=args.limit)
    except OSError as exc:
        emit({"error": str(exc)}, args.json, f"could not reach AutoEq index: {exc}")
        return EXIT_DATA
    if not results:
        emit([], args.json, "no match")
        return EXIT_DATA
    if args.json:
        emit(results, True)
    else:
        for n, r in enumerate(results):
            # ASCII only: Windows consoles default to a codepage that mangles dashes.
            print(f"{n:>2}  {r['name']}  [{r['source']}" + (f" / {r['rig']}]" if r["rig"] else "]"))
    return EXIT_OK


def cmd_eq_apply(args):
    try:
        results = eqdb.search(args.query, limit=max(args.index + 1, 5))
        if not results:
            emit({"error": "no match"}, args.json, "no match")
            return EXIT_DATA
        entry = results[args.index]
        prof = eqdb.profile(entry)
    except (OSError, ValueError, IndexError) as exc:
        emit({"error": str(exc)}, args.json, f"could not load profile: {exc}")
        return EXIT_DATA
    with connect(args) as dac:
        out = {"profile": entry["name"], "source": entry["source"], **_apply_profile(dac, prof, args)}
    emit(out, args.json, f"applied '{entry['name']}' ({entry['source']}): "
         f"{out['applied']} bands, preamp {out['preamp']:+.1f} dB"
         + (f", dropped {len(out['dropped'])} (device has {out['applied']} bands)"
            if out["dropped"] else ""))
    return EXIT_OK


def cmd_peq_import(args):
    try:
        prof = eqdb.parse(open(args.file, encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        emit({"error": str(exc)}, args.json, f"cannot read profile: {exc}")
        return EXIT_DATA
    with connect(args) as dac:
        out = {"file": args.file, **_apply_profile(dac, prof, args)}
    emit(out, args.json, f"imported {args.file}: {out['applied']} bands, "
         f"preamp {out['preamp']:+.1f} dB")
    return EXIT_OK


def cmd_peq_export(args):
    with connect(args) as dac:
        bands = [dac.get_band(i) for i in range(dac.band_count())]
        text = eqdb.render(dac.get("eq-gain"), [b for b in bands if b])
    if args.file:
        with open(args.file, "w", encoding="utf-8") as fh:
            fh.write(text)
        emit({"file": args.file, "bands": len(bands)}, args.json, f"wrote {args.file}")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_peq_clear(args):
    with connect(args) as dac:
        count = dac.band_count()
        dac.set("eq-gain", 0.0)
        for i in range(count):
            dac.set_band(i, 1000, 0.0, 0.7, "peak")
        emit({"flattened": count}, args.json, f"flattened {count} bands")
    return EXIT_OK


def known_command_names():
    """cmd id -> the name fiioctl knows it by, for annotating a scan."""
    names = {cmd.id: name for name, cmd in registry.COMMANDS.items()}
    names[registry.BAND_CMD] = "peq-band"
    names[registry.NAME_CMD] = "preset-name"
    return names


def diff_scans(before, after):
    """Compare two {cmd: hex} snapshots. Returns rows of (cmd, before, after).

    Keys arrive as strings from JSON, so sort them numerically - otherwise
    command 14 lists before command 3.
    """
    def as_int(key):
        try:
            return int(key, 0)
        except (TypeError, ValueError):
            return 1 << 32

    rows = []
    for cmd in sorted(set(before) | set(after), key=as_int):
        was, now = before.get(cmd), after.get(cmd)
        if was != now:
            rows.append((as_int(cmd), was, now))
    return rows


def cmd_scan(args):
    names = known_command_names()
    if args.diff and not os.path.exists(args.diff):
        emit({"error": f"no such snapshot: {args.diff}"}, args.json,
             f"no such snapshot: {args.diff}")
        return EXIT_USAGE
    with connect(args) as dac:
        found = dac.scan(limit=args.max, timeout=args.timeout)
        product = dac.info.get("product_string")
    snapshot = {hex(c): p.hex(" ") for c, p in found.items()}

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump({"product": product, "commands": {str(c): p.hex(" ")
                                                        for c, p in found.items()}},
                      fh, indent=2)

    if args.diff:
        with open(args.diff, encoding="utf-8") as fh:
            before = json.load(fh).get("commands", {})
        now = {str(c): p.hex(" ") for c, p in found.items()}
        rows = diff_scans(before, now)
        out = [{"cmd": c, "name": names.get(c, ""), "before": b, "after": a}
               for c, b, a in rows]
        if args.json:
            emit(out, True)
        elif not rows:
            print("no differences")
        else:
            for row in out:
                print(f"cmd {row['cmd']:>3} ({row['cmd']:#04x})  "
                      f"{row['before'] or '(silent)'}  ->  {row['after'] or '(silent)'}"
                      + (f"   {row['name']}" if row["name"] else "   <unknown>"))
        return EXIT_OK

    if args.json:
        emit({"product": product, "skipped": registry.DESTRUCTIVE,
              "commands": [{"cmd": c, "name": names.get(c, ""), "payload": p.hex(" ")}
                           for c, p in found.items()]}, True)
        return EXIT_OK

    print(f"{product}: {len(found)} of {args.max} commands answered "
          f"({len(registry.DESTRUCTIVE)} skipped as destructive)\n")
    for cmd, payload in found.items():
        label = names.get(cmd, "<unknown>")
        print(f"{cmd:>4} {cmd:#04x}  {len(payload):>2}B  {payload.hex(' '):<28} {label}")
    unknown = [c for c in found if c not in names]
    print(f"\n{len(unknown)} answered but undecoded: "
          f"{', '.join(str(c) for c in unknown) or 'none'}")
    silent = [n for c, n in names.items() if c not in found and c not in registry.DESTRUCTIVE]
    print(f"{len(silent)} known commands silent on this device: {', '.join(silent) or 'none'}")
    return EXIT_OK


def cmd_raw(args):
    data = bytes.fromhex(args.data) if args.data else b""
    hdr = proto.GET if args.read else proto.SET
    with connect(args) as dac:
        if args.read:
            payload = dac.request(args.cmd, data, hdr=hdr)
            emit({"cmd": args.cmd, "payload": payload.hex(" "), "bytes": list(payload)}, args.json)
        else:
            dac.send(args.cmd, data, hdr=hdr)
            emit({"cmd": args.cmd, "sent": proto.frame(hdr, args.cmd, data).hex(" ")}, args.json)
    return EXIT_OK


def _cloud_presets(args, dac):
    from . import cloud
    if not dac.spec.cloud_id:
        raise cloud.CloudError(f"{dac.spec.name} has no known FiiO catalogue id")
    return cloud.presets(dac.spec.cloud_id, args.query or "",
                         official=not args.community_only,
                         community=not args.official_only)


def cmd_cloud_list(args):
    from .cloud import CloudError
    with connect(args) as dac:
        try:
            found = _cloud_presets(args, dac)
        except CloudError as exc:
            emit({"error": str(exc)}, args.json, f"error: {exc}")
            return EXIT_DATA
    if args.json:
        emit([{k: v for k, v in p.items() if k != "bands"} | {"bands": len(p["bands"])}
              for p in found], True)
    else:
        for n, p in enumerate(found):
            by = f" by {p['rig']}" if p["rig"] else ""
            print(f"{n:>3}  {p['name'][:42]:42} [{p['source']}]{by}  "
                  f"{len(p['bands'])} bands, preamp {p['preamp']:+.1f} dB, {p['downloads']} dl")
    return EXIT_OK


def cmd_cloud_show(args):
    from . import curve
    from .cloud import CloudError
    with connect(args) as dac:
        try:
            found = _cloud_presets(args, dac)
        except CloudError as exc:
            emit({"error": str(exc)}, args.json, f"error: {exc}")
            return EXIT_DATA
    if not 0 <= args.index < len(found):
        emit({"error": "index out of range"}, args.json, "index out of range")
        return EXIT_USAGE
    preset = found[args.index]
    if args.json:
        emit(preset, True)
        return EXIT_OK
    print(f"{preset['name']}  [{preset['source']}]")
    if preset["description"]:
        print(f"  {preset['description']}")
    print(f"  preamp {preset['preamp']:+.1f} dB")
    for line in curve.plot(preset["bands"], height=9, width=52):
        print(line)
    for b in preset["bands"]:
        print(f"   {b['freq']:>6} Hz  {b['gain']:+6.1f} dB  Q {b['q']:>5.2f}  {b['filter']}")
    return EXIT_OK


def cmd_cloud_apply(args):
    from .cloud import CloudError
    with connect(args) as dac:
        try:
            found = _cloud_presets(args, dac)
        except CloudError as exc:
            emit({"error": str(exc)}, args.json, f"error: {exc}")
            return EXIT_DATA
        if not 0 <= args.index < len(found):
            emit({"error": "index out of range"}, args.json, "index out of range")
            return EXIT_USAGE
        preset = found[args.index]
        out = {"profile": preset["name"], "source": preset["source"],
               **_apply_profile(dac, preset, args)}
    emit(out, args.json, f"applied '{preset['name']}' ({preset['source']}): "
         f"{out['applied']} bands, preamp {out['preamp']:+.1f} dB")
    return EXIT_OK


def cmd_autoeq_refresh(args):
    from .cloud import CloudError, refresh_indexes
    try:
        counts = refresh_indexes()
    except CloudError as exc:
        emit({"error": str(exc)}, args.json, f"error: {exc}")
        return EXIT_DATA
    emit(counts, args.json,
         f"refreshed: {counts['measurements']} measurements, {counts['targets']} targets")
    return EXIT_OK


def cmd_autoeq_search(args):
    from .cloud import CloudError, find_measurements
    try:
        rows = find_measurements(args.query, limit=args.limit)
    except CloudError as exc:
        emit({"error": str(exc)}, args.json, f"error: {exc}")
        return EXIT_DATA
    if not rows:
        emit([], args.json, "no match")
        return EXIT_DATA
    if args.json:
        emit(rows, True)
    else:
        for row in rows:
            variant = f"  [{row['config']}]" if row.get("config") else ""
            print(f"{row['id']:>6}  {row['productName']}{variant}"
                  f"  —  {row.get('source') or '?'}")
    return EXIT_OK


def cmd_autoeq_targets(args):
    from .cloud import CloudError, target_list
    try:
        rows = target_list()
    except CloudError as exc:
        emit({"error": str(exc)}, args.json, f"error: {exc}")
        return EXIT_DATA
    if args.query:
        needle = args.query.lower()
        rows = [r for r in rows if needle in (r.get("label") or "").lower()]
    rows = rows[: args.limit]
    if args.json:
        emit(rows, True)
    else:
        for row in rows:
            print(f"{row['id']:>6}  {row.get('label')}")
    return EXIT_OK


def _autoeq_profile(args, bands):
    from .cloud import auto_eq
    return auto_eq(args.id, args.target, bands=bands)


def cmd_autoeq_show(args):
    from . import curve
    from .cloud import CloudError
    try:
        profile = _autoeq_profile(args, args.bands)
    except CloudError as exc:
        emit({"error": str(exc)}, args.json, f"error: {exc}")
        return EXIT_DATA
    if args.json:
        emit(profile, True)
        return EXIT_OK
    print(f"{profile['name']}  [{profile['source']}]")
    print(f"  target: {profile['target_label']}  (id {profile['target_id']})")
    print(f"  preamp {profile['preamp']:+.1f} dB")
    for line in curve.plot(profile["bands"], height=9, width=52):
        print(line)
    for b in profile["bands"]:
        print(f"   {b['freq']:>6} Hz  {b['gain']:+6.1f} dB  Q {b['q']:>5.2f}  {b['filter']}")
    return EXIT_OK


def cmd_autoeq_apply(args):
    from .cloud import CloudError
    with connect(args) as dac:
        try:
            profile = _autoeq_profile(args, dac.band_count())
        except CloudError as exc:
            emit({"error": str(exc)}, args.json, f"error: {exc}")
            return EXIT_DATA
        out = {"profile": profile["name"], "target": profile["target_label"],
               **_apply_profile(dac, profile, args)}
    emit(out, args.json, f"applied '{profile['name']}' to {out['applied']} bands "
         f"(target {profile['target_label']}), preamp {out['preamp']:+.1f} dB")
    return EXIT_OK


def cmd_ui(args):
    try:
        from .ui import run
    except ImportError:
        emit({"error": "textual not installed"}, args.json,
             "the TUI needs textual:  pip install 'fiioctl[ui]'")
        return EXIT_USAGE
    if args.dry_run:
        emit({"error": "--dry-run is not supported by the TUI"}, args.json,
             "the TUI needs to read from the device; --dry-run cannot read")
        return EXIT_USAGE
    with connect(args) as dac:
        return run(dac)


def cmd_schema(args):
    """Machine-readable description of the whole tool, for agents and scripts."""
    emit(
        {
            "framing": {
                "set_header": list(proto.SET),
                "get_header": list(proto.GET),
                "layout": "[hdr0, hdr1, 0x00, 0x00, cmd, len, *data, 0x00, 0xEE]",
                "byte_order": "big-endian",
                "terminator": proto.TERM,
            },
            "commands": {
                name: {
                    "id": c.id, "codec": c.codec, "mode": c.mode, "help": c.help,
                    "range": list(c.rng) if c.rng else None,
                    "enum": c.enum, "capability": registry.CAPS.get(name),
                }
                for name, c in registry.COMMANDS.items()
            },
            "band_command": {"id": registry.BAND_CMD,
                             "payload": "[index, gain*10 (s16), freq (u16), q*100 (u16), filter]"},
            "name_command": {"id": registry.NAME_CMD, "payload": "[slot, *utf8]"},
            "filters": proto.FILTERS,
            "unmapped_commands": registry.UNMAPPED,
            "devices": {
                key: {"name": d.name, "report_id": d.report_id, "interval_s": d.interval,
                      "gain_range": list(d.gain_range), "bands": d.bands,
                      "presets": d.presets, "capabilities": list(d.caps),
                      "renameable_slots": list(d.name_slots),
                      "unsupported": list(d.unsupported)}
                for key, d in registry.DEVICES.items()
            },
            "exit_codes": {"ok": EXIT_OK, "usage": EXIT_USAGE,
                           "device": EXIT_DEVICE, "data": EXIT_DATA},
            "batch_protocol": {
                "stdin": 'one JSON object per line: {"op":"get|set|band|raw","name":...,"value":...}',
                "stdout": "one JSON object per line, with an 'ok' boolean",
            },
        },
        True,
    )
    return EXIT_OK


def cmd_batch(args):
    """Stream many operations over one open connection (amortises device open)."""
    with connect(args) as dac:
        for line in sys.stdin:
            line = line.strip().lstrip("﻿")  # tolerate a BOM from Windows pipes
            if not line or line.startswith("#"):
                continue
            try:
                req = json.loads(line)
                op = req.get("op", "get")
                if op == "get":
                    res = {"ok": True, "value": dac.get(req["name"])}
                elif op == "set":
                    res = {"ok": True, "value": dac.set(req["name"], req["value"])}
                elif op == "band":
                    if "freq" in req:
                        dac.set_band(req["index"], req["freq"], req["gain"],
                                     req.get("q", 0.7), req.get("filter", "peak"))
                        res = {"ok": True}
                    else:
                        res = {"ok": True, "value": dac.get_band(req["index"])}
                elif op == "raw":
                    payload = dac.request(req["cmd"], bytes.fromhex(req.get("data", "")))
                    res = {"ok": True, "value": payload.hex(" ")}
                else:
                    res = {"ok": False, "error": f"unknown op {op!r}"}
            except (FiioError, KeyError, ValueError, TypeError) as exc:
                res = {"ok": False, "error": str(exc)}
            json.dump({**res, "request": line}, sys.stdout)
            sys.stdout.write("\n")
            sys.stdout.flush()
    return EXIT_OK


# -- parser -------------------------------------------------------------------


def _group_help(parser):
    """Make a bare command group print its own help instead of erroring."""

    def show(_args):
        parser.print_help()
        return EXIT_OK

    return show


def build_parser():
    p = argparse.ArgumentParser(
        prog="fiioctl", description="Control FiiO USB DACs over HID.",
        epilog="Run 'fiioctl schema' for a machine-readable description of every command.")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--trace", action="store_true", help="log every HID packet to stdout")
    p.add_argument("--serial", help="select device by USB serial number")
    p.add_argument("--product", help="select device by product-string substring")
    p.add_argument("--dry-run", action="store_true",
                   help="build and show packets without writing to the device")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")
    p.set_defaults(fn=_group_help(p))

    sub.add_parser("devices", help="list connected FiiO HID interfaces").set_defaults(fn=cmd_devices)
    sub.add_parser("info", help="identify the device and its capabilities").set_defaults(fn=cmd_info)
    st = sub.add_parser("state", help="read every supported setting")
    st.add_argument("--all", action="store_true", help="probe commands outside the capability list")
    st.set_defaults(fn=cmd_state)

    names = sorted(registry.COMMANDS)
    g = sub.add_parser("get", help="read one setting")
    g.add_argument("name", choices=names)
    g.set_defaults(fn=cmd_get)
    s = sub.add_parser("set", help="write one setting")
    s.add_argument("name", choices=[n for n in names if registry.COMMANDS[n].writable])
    s.add_argument("value")
    s.set_defaults(fn=cmd_set)

    eq = sub.add_parser("eq", help="presets, gain and the headphone profile database")
    eqs = eq.add_subparsers(dest="eqcmd", metavar="SUBCOMMAND")
    eq.set_defaults(fn=_group_help(eq))
    pr = eqs.add_parser("preset", help="get or set the active preset")
    pr.add_argument("value", nargs="?")
    pr.set_defaults(fn=cmd_eq_preset)
    nm = eqs.add_parser("name", help="get or set a user preset's name")
    nm.add_argument("slot", type=int)
    nm.add_argument("name_value", nargs="?", metavar="NAME")
    nm.set_defaults(fn=cmd_eq_name)
    sv = eqs.add_parser("save", help="persist the current EQ into a slot")
    sv.add_argument("slot", nargs="?", type=int)
    sv.set_defaults(fn=cmd_eq_save)
    se = eqs.add_parser("search", help="search the AutoEq headphone database")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=20)
    se.set_defaults(fn=cmd_eq_search)
    ap = eqs.add_parser("apply", help="apply a headphone profile from the database")
    ap.add_argument("query")
    ap.add_argument("--index", type=int, default=0, help="which search result to use")
    ap.add_argument("--slot", help="switch to this preset slot first (e.g. user1)")
    ap.add_argument("--save", action="store_true", help="persist to the device after applying")
    ap.set_defaults(fn=cmd_eq_apply)

    peq = sub.add_parser("peq", help="parametric EQ bands")
    peqs = peq.add_subparsers(dest="peqcmd", metavar="SUBCOMMAND")
    peq.set_defaults(fn=_group_help(peq))
    peqs.add_parser("show", help="read all bands").set_defaults(fn=cmd_peq_show)
    bs = peqs.add_parser("set", help="write one band")
    bs.add_argument("band", type=int)
    bs.add_argument("--freq", type=float, required=True)
    bs.add_argument("--gain", type=float, required=True)
    bs.add_argument("--q", type=float, default=0.7)
    bs.add_argument("--filter", default="peak", choices=sorted(proto.FILTERS))
    bs.set_defaults(fn=cmd_peq_set)
    im = peqs.add_parser("import", help="load a ParametricEQ/AutoEq .txt file")
    im.add_argument("file")
    im.add_argument("--slot", help="switch to this preset slot first")
    im.add_argument("--save", action="store_true")
    im.set_defaults(fn=cmd_peq_import)
    ex = peqs.add_parser("export", help="dump current bands as ParametricEQ text")
    ex.add_argument("file", nargs="?")
    ex.set_defaults(fn=cmd_peq_export)
    peqs.add_parser("clear", help="flatten every band").set_defaults(fn=cmd_peq_clear)

    cl = sub.add_parser("cloud", help="FiiO's online preset catalogue for this device")
    cls = cl.add_subparsers(dest="cloudcmd", metavar="SUBCOMMAND")
    cl.set_defaults(fn=_group_help(cl))
    for name, fn, helptext in (("list", cmd_cloud_list, "list presets published for this model"),
                               ("show", cmd_cloud_show, "print one preset's bands and curve"),
                               ("apply", cmd_cloud_apply, "write one preset to the device")):
        p2 = cls.add_parser(name, help=helptext)
        if name != "list":
            p2.add_argument("index", type=int, help="row number from `cloud list`")
        p2.add_argument("--query", help="filter by name")
        p2.add_argument("--official-only", action="store_true")
        p2.add_argument("--community-only", action="store_true")
        if name == "apply":
            p2.add_argument("--slot", help="switch to this preset slot first")
            p2.add_argument("--save", action="store_true", help="persist after applying")
        p2.set_defaults(fn=fn)

    ae = sub.add_parser("autoeq", help="FiiO's headphone measurement database (~4500 models)")
    aes = ae.add_subparsers(dest="autoeqcmd", metavar="SUBCOMMAND")
    ae.set_defaults(fn=_group_help(ae))
    aes.add_parser("refresh", help="re-download the measurement and target indexes"
                   ).set_defaults(fn=cmd_autoeq_refresh)
    aq = aes.add_parser("search", help="find a headphone by name")
    aq.add_argument("query")
    aq.add_argument("--limit", type=int, default=25)
    aq.set_defaults(fn=cmd_autoeq_search)
    at = aes.add_parser("targets", help="list target curves")
    at.add_argument("query", nargs="?")
    at.add_argument("--limit", type=int, default=40)
    at.set_defaults(fn=cmd_autoeq_targets)
    for name, fn, helptext in (("show", cmd_autoeq_show, "compute and print a profile"),
                               ("apply", cmd_autoeq_apply, "compute and write to the device")):
        p2 = aes.add_parser(name, help=helptext)
        p2.add_argument("id", type=int, help="measurement id from `autoeq search`")
        p2.add_argument("--target", type=int,
                        help="target curve id (default: the one FiiO recommends)")
        if name == "show":
            p2.add_argument("--bands", type=int, default=10)
        else:
            p2.add_argument("--slot", help="switch to this preset slot first")
            p2.add_argument("--save", action="store_true", help="persist after applying")
        p2.set_defaults(fn=fn)

    sc = sub.add_parser("scan", help="probe the command space read-only, and diff snapshots")
    sc.add_argument("--max", type=int, default=256, help="highest command id to probe")
    sc.add_argument("--timeout", type=float, default=0.25, help="seconds to wait per command")
    sc.add_argument("--save", metavar="FILE", help="write the scan to a snapshot file")
    sc.add_argument("--diff", metavar="FILE",
                    help="compare against a snapshot instead of listing everything")
    sc.set_defaults(fn=cmd_scan)

    rw = sub.add_parser("raw", help="send an arbitrary command (escape hatch)")
    rw.add_argument("cmd", type=lambda v: int(v, 0), metavar="CMD")
    rw.add_argument("data", nargs="?", help="hex payload, e.g. 0a1b")
    rw.add_argument("--read", action="store_true", help="use the GET header and await a reply")
    rw.set_defaults(fn=cmd_raw)

    sub.add_parser("ui", help="interactive terminal UI").set_defaults(fn=cmd_ui)
    sub.add_parser("schema", help="dump the protocol/command model as JSON").set_defaults(fn=cmd_schema)
    sub.add_parser("batch", help="stream JSON operations from stdin").set_defaults(fn=cmd_batch)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except FiioError as exc:
        emit({"error": str(exc)}, args.json, f"error: {exc}")
        return EXIT_DEVICE
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
