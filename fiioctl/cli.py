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

from . import __version__, curve, eqdb, library, proto, registry
from .device import Dac, FiioError, candidates

EXIT_OK, EXIT_USAGE, EXIT_DEVICE, EXIT_DATA = 0, 2, 3, 4


class Abort(Exception):
    """Stop a subcommand with a message and an exit code.

    Saves every command from writing its own `emit(...); return EXIT_...` pair,
    and from having to remember which code belongs to which kind of failure.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


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


def labelled(name, value, **extra):
    """A result dict carrying the raw value plus its name, when it has one.

    Machines compare the number; people read the label. Both commands that write
    or read a single setting need exactly this.
    """
    out = {name: value, **extra}
    text = registry.label(name, value)
    if text is not None:
        out["label"] = text
    return out


def data_errors():
    """Exception types that mean "bad or unreachable data", for main() to report.

    Built lazily because `cloud` needs an optional dependency, and importing it
    at module scope would make the whole CLI require `cryptography`.
    """
    try:
        from .cloud import CloudError
    except ImportError:
        return (ValueError, TypeError, OverflowError)
    return (CloudError, ValueError, TypeError, OverflowError)


def connect(args):
    return Dac.open(serial=args.serial, product=args.product, trace=args.trace,
                    dry=args.dry_run, force=getattr(args, "force", False))


# -- individual commands ------------------------------------------------------


def cmd_devices(args):
    rows = [
        {
            "product": i.get("product_string"),
            "transport": registry.match(i.get("product_string")).transport,
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
        f"usage={r['usage_page']} iface={r['interface']}"
        + ("" if r["transport"] == "hid" else f"   [{r['transport']}: not supported]")
        for r in rows))
    return EXIT_OK


def cmd_info(args):
    with connect(args) as dac:
        spec = dac.spec
        out = {
            "product": dac.info.get("product_string"),
            "serial": dac.info.get("serial_number"),
            "matched_profile": spec.name,
            "transport": spec.transport,
            "verified_on_hardware": spec.verified,
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
    emit(labelled(args.name, value), args.json,
         None if args.json else str(shown(args.name, value)))
    return EXIT_OK


def cmd_set(args):
    with connect(args) as dac:
        # read back by default: the firmware accepts a write silently even when
        # it ignores or clamps the value
        value = dac.set(args.name, args.value, verify=not args.no_verify)
    emit(labelled(args.name, value, written=True), args.json,
         f"{args.name} = {shown(args.name, value)}")
    return EXIT_OK


def cmd_eq_preset(args):
    with connect(args) as dac:
        # Dac.set resolves preset names itself, so there is one place that knows
        # how a device's slots are named
        if args.value is None:
            value, written = dac.get("eq-preset"), False
        else:
            value, written = dac.set("eq-preset", args.value, verify=True), True
        name = next((k for k, v in dac.spec.presets.items() if v == value), None)
    out = {"eq-preset": value}
    if name:
        out["label"] = name
    if written:
        out["written"] = True
    human = f"{name} ({value})" if name else str(value)
    emit(out, args.json, f"eq-preset = {human}" if written else human)
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
        band = dac.set_band(args.band, args.freq, args.gain, args.q, args.filter)
        emit({**band, "written": True}, args.json,
             f"band {band['index']}: {band['freq']} Hz {band['gain']:+} dB "
             f"Q {band['q']} {band['filter']}")
    return EXIT_OK


def _apply_profile(dac, prof, args):
    """Write a parsed ParametricEQ profile onto the device's bands."""
    count = dac.band_count()
    bands = prof["bands"][:count]
    dropped = prof["bands"][count:]
    slot = None if args.slot is None else dac.resolve_preset(args.slot)
    save_to = slot
    if args.save:
        # before anything is written, not after the EQ has already changed. A dry
        # run cannot read the active preset, but should still show its frames.
        if slot is None and not dac.dry:
            save_to = dac.get("eq-preset")
        if save_to is not None:
            dac.require_user_slot(save_to)
    if slot is not None:
        dac.set("eq-preset", slot)
    preamp, _ = dac.write_profile(prof["preamp"], bands, count)
    if args.save:
        dac.set("eq-save", dac.get("eq-preset") if save_to is None else save_to)
    return {
        "applied": len(bands),
        "flattened": count - len(bands),
        "dropped": [f"{b['freq']}Hz" for b in dropped],
        "preamp": preamp,
        "preamp_clamped": preamp != prof["preamp"],
        "saved": bool(args.save),
    }


def _catalogue_id(args):
    """FiiO catalogue id of the model in play, without opening the device."""
    if args.product:
        return registry.match(args.product).cloud_id
    try:
        infos = candidates()
    except FiioError:
        return None
    ids = (registry.match(i.get("product_string")).cloud_id for i in infos
           if not args.serial or i.get("serial_number") == args.serial)
    return next((cloud_id for cloud_id in ids if cloud_id), None)


def _search_library(query, cloud_id, limit):
    """Search everything, warning on stderr about any source that failed."""
    found, problems = library.search(query, cloud_id, limit=limit)
    if problems and not found:
        raise Abort(EXIT_DATA, "; ".join(problems))
    for problem in problems:
        print(f"warning: {problem}", file=sys.stderr)
    return found


def cmd_eq_search(args):
    cloud_id = _catalogue_id(args)
    if not cloud_id:
        print("warning: no known device connected, so FiiO's presets for it were not "
              "searched; name a model with --product", file=sys.stderr)
    results = _search_library(args.query, cloud_id, args.limit)
    if not results:
        emit([], args.json, "no match")
        return EXIT_DATA
    if args.json:
        emit([{"index": n, "id": r["id"], "name": r["name"], "source": r["source"],
               "rig": r["rig"], "bands": len(r["bands"]) if r["bands"] else None}
              for n, r in enumerate(results)], True)
    else:
        for n, r in enumerate(results):
            # ASCII only: Windows consoles default to a codepage that mangles dashes.
            print(f"{n:>2}  {r['name']}  [{r['source']}" + (f" / {r['rig']}]" if r["rig"] else "]"))
    return EXIT_OK


def _choose_result(args, cloud_id):
    """The result `eq apply` means: an id from `eq search`, or a query and index."""
    named = library.lookup(args.query, cloud_id)
    if named is not None:
        return named
    results = _search_library(args.query, cloud_id,
                              max(args.index + 1, library.DEFAULT_LIMIT))
    if not results:
        raise Abort(EXIT_DATA, "no match")
    if not 0 <= args.index < len(results):
        raise Abort(EXIT_USAGE, f"no result {args.index}; the search found {len(results)}")
    return results[args.index]


def cmd_eq_apply(args):
    with connect(args) as dac:
        count = dac.band_count()
        try:
            result = library.resolve(_choose_result(args, dac.spec.cloud_id), count)
        except OSError as exc:
            raise Abort(EXIT_DATA, f"could not load profile: {exc}") from exc
        out = {"profile": result["name"], "source": result["source"], "id": result["id"],
               **_apply_profile(dac, result, args)}
    emit(out, args.json, f"applied '{result['name']}' ({result['source']}): "
         f"{out['applied']} bands, preamp {out['preamp']:+.1f} dB"
         + (f", dropped {len(out['dropped'])} (device has {out['applied']} bands)"
            if out["dropped"] else ""))
    return EXIT_OK


def cmd_peq_import(args):
    try:
        with open(args.file, encoding="utf-8") as fh:
            prof = eqdb.parse(fh.read())
    except (OSError, ValueError) as exc:
        raise Abort(EXIT_DATA, f"cannot read profile: {exc}") from exc
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


def _as_hex(found):
    """A scan's {cmd: payload} as the {str(cmd): hex} form snapshots are stored in."""
    return {str(cmd): payload.hex(" ") for cmd, payload in found.items()}


def _report_diff(args, found, names):
    with open(args.diff, encoding="utf-8") as fh:
        before = json.load(fh).get("commands", {})
    rows = diff_scans(before, _as_hex(found))
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


def _report_scan(args, found, names, product):
    if args.json:
        emit({"product": product, "skipped": registry.DESTRUCTIVE,
              "commands": [{"cmd": c, "name": names.get(c, ""), "payload": p.hex(" ")}
                           for c, p in found.items()]}, True)
        return
    print(f"{product}: {len(found)} of {args.max} commands answered "
          f"({len(registry.DESTRUCTIVE)} skipped as destructive)\n")
    for cmd, payload in found.items():
        print(f"{cmd:>4} {cmd:#04x}  {len(payload):>2}B  {payload.hex(' '):<28} "
              f"{names.get(cmd, '<unknown>')}")
    unknown = [c for c in found if c not in names]
    print(f"\n{len(unknown)} answered but undecoded: "
          f"{', '.join(str(c) for c in unknown) or 'none'}")
    silent = [n for c, n in names.items() if c not in found and c not in registry.DESTRUCTIVE]
    print(f"{len(silent)} known commands silent on this device: {', '.join(silent) or 'none'}")


def cmd_scan(args):
    names = known_command_names()
    if args.diff and not os.path.exists(args.diff):
        raise Abort(EXIT_USAGE, f"no such snapshot: {args.diff}")
    with connect(args) as dac:
        found = dac.scan(limit=args.max, timeout=args.timeout)
        product = dac.info.get("product_string")

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump({"product": product, "commands": _as_hex(found)}, fh, indent=2)

    if args.diff:
        _report_diff(args, found, names)
    else:
        _report_scan(args, found, names, product)
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


def print_profile(profile):
    """Human-readable dump of a profile: heading, curve, then the bands."""
    print(f"{profile['name']}  [{profile['source']}]")
    if profile.get("description"):
        print(f"  {profile['description']}")
    print(f"  preamp {profile['preamp']:+.1f} dB")
    for line in curve.plot(profile["bands"], height=9, width=52):
        print(line)
    for band in profile["bands"]:
        print(f"   {band['freq']:>6} Hz  {band['gain']:+6.1f} dB  "
              f"Q {band['q']:>5.2f}  {band['filter']}")


def _cloud_presets(args, dac):
    from . import cloud
    if not dac.spec.cloud_id:
        raise cloud.CloudError(f"{dac.spec.name} has no known FiiO catalogue id")
    return cloud.presets(dac.spec.cloud_id, args.query or "",
                         official=not args.community_only,
                         community=not args.official_only)


def cmd_cloud_list(args):
    with connect(args) as dac:
        found = _cloud_presets(args, dac)
    if args.json:
        emit([{k: v for k, v in p.items() if k != "bands"} | {"bands": len(p["bands"])}
              for p in found], True)
    else:
        for n, p in enumerate(found):
            by = f" by {p['rig']}" if p["rig"] else ""
            print(f"{n:>3}  {p['name'][:42]:42} [{p['source']}]{by}  "
                  f"{len(p['bands'])} bands, preamp {p['preamp']:+.1f} dB, {p['downloads']} dl")
    return EXIT_OK


def _pick_preset(args, dac):
    """The preset `args.index` names, or stop with a usage error."""
    found = _cloud_presets(args, dac)
    if not 0 <= args.index < len(found):
        raise Abort(EXIT_USAGE,
                    f"no preset {args.index}; `cloud list` showed {len(found)}")
    return found[args.index]


def cmd_cloud_show(args):
    with connect(args) as dac:
        preset = _pick_preset(args, dac)
    if args.json:
        emit(preset, True)
        return EXIT_OK
    print_profile(preset)
    return EXIT_OK


def cmd_cloud_apply(args):
    with connect(args) as dac:
        preset = _pick_preset(args, dac)
        out = {"profile": preset["name"], "source": preset["source"],
               **_apply_profile(dac, preset, args)}
    emit(out, args.json, f"applied '{preset['name']}' ({preset['source']}): "
         f"{out['applied']} bands, preamp {out['preamp']:+.1f} dB")
    return EXIT_OK


def cmd_autoeq_refresh(args):
    from .cloud import refresh_indexes
    counts = refresh_indexes()
    emit(counts, args.json,
         f"refreshed: {counts['measurements']} measurements, {counts['targets']} targets")
    return EXIT_OK


def cmd_autoeq_search(args):
    from .cloud import find_measurements
    rows = find_measurements(args.query, limit=args.limit)
    if not rows:
        emit([], args.json, "no match")
        return EXIT_DATA
    if args.json:
        emit(rows, True)
    else:
        for row in rows:
            variant = f"  [{row['config']}]" if row.get("config") else ""
            print(f"{row['id']:>6}  {row['productName']}{variant}"
                  f"  -  {row.get('source') or '?'}")
    return EXIT_OK


def cmd_autoeq_targets(args):
    from .cloud import target_list
    rows = target_list()
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
    profile = _autoeq_profile(args, args.bands)
    if args.json:
        emit(profile, True)
        return EXIT_OK
    print_profile(profile)
    return EXIT_OK


def cmd_autoeq_apply(args):
    with connect(args) as dac:
        profile = _autoeq_profile(args, dac.band_count())
        out = {"profile": profile["name"], "target": profile["target_label"],
               **_apply_profile(dac, profile, args)}
    emit(out, args.json, f"applied '{profile['name']}' to {out['applied']} bands "
         f"(target {profile['target_label']}), preamp {out['preamp']:+.1f} dB")
    return EXIT_OK


def cmd_ui(args):
    try:
        from .ui import run
    except ImportError as exc:
        raise Abort(EXIT_USAGE,
                    "the TUI needs textual:  pip install 'fiioctl[ui]'") from exc
    if args.dry_run:
        raise Abort(EXIT_USAGE,
                    "the TUI needs to read from the device; --dry-run cannot read")
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
                      "unsupported": list(d.unsupported),
                      "transport": d.transport, "verified": d.verified}
                for key, d in registry.DEVICES.items()
            },
            "exit_codes": {"ok": EXIT_OK, "usage": EXIT_USAGE,
                           "device": EXIT_DEVICE, "data": EXIT_DATA},
            "batch_protocol": {
                "stdin": ('one JSON object per line: '
                          '{"op":"get|set|band|raw","name":...,"value":...}'),
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
                if not isinstance(req, dict):
                    raise ValueError("expected a JSON object")
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
            except (FiioError, KeyError, ValueError, TypeError, OverflowError) as exc:
                res = {"ok": False, "error": str(exc)}
            json.dump({**res, "request": line}, sys.stdout)
            sys.stdout.write("\n")
            sys.stdout.flush()
    return EXIT_OK


# -- parser -------------------------------------------------------------------


def add_apply_flags(parser):
    """The two options every command that writes a profile to the device takes."""
    parser.add_argument("--slot", help="switch to this preset slot first")
    parser.add_argument("--save", action="store_true", help="persist after applying")


def _group_help(parser):
    """Make a bare command group print its own help instead of erroring."""

    def show(_args):
        parser.print_help()
        return EXIT_OK

    return show


class SubParser(argparse.ArgumentParser):
    """A subcommand parser that also takes --json, as the docs say every one does.

    The default is suppressed so a subcommand that was not given the flag leaves
    alone a --json already seen before it.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                          help="emit JSON")


def build_parser():
    p = argparse.ArgumentParser(
        prog="fiioctl", description="Control FiiO USB DACs over HID.",
        epilog="Run 'fiioctl schema' for a machine-readable description of every command.")
    p.add_argument("--version", action="version", version=f"fiioctl {__version__}")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--trace", action="store_true", help="log every HID packet to stderr")
    p.add_argument("--serial", help="select device by USB serial number")
    p.add_argument("--product", help="select device by product-string substring")
    p.add_argument("--dry-run", action="store_true",
                   help="build and show packets without writing to the device")
    p.add_argument("--force", action="store_true",
                   help="talk to a model whose transport is unsupported anyway")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND", parser_class=SubParser)
    p.set_defaults(fn=_group_help(p))

    sub.add_parser("devices", help="list connected FiiO HID interfaces"
                   ).set_defaults(fn=cmd_devices)
    sub.add_parser("info", help="identify the device and its capabilities"
                   ).set_defaults(fn=cmd_info)
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
    s.add_argument("--no-verify", action="store_true",
                   help="skip reading the value back after writing")
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
    se = eqs.add_parser("search", help="search FiiO's presets, its measurements and AutoEq")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=library.DEFAULT_LIMIT)
    se.set_defaults(fn=cmd_eq_search)
    ap = eqs.add_parser("apply", help="apply a result from `eq search`, whatever its source")
    ap.add_argument("query", help="the same search, or a result's id from `eq search --json`")
    ap.add_argument("--index", type=int, default=0, help="which search result to use")
    add_apply_flags(ap)
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
    add_apply_flags(im)
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
            add_apply_flags(p2)
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
            add_apply_flags(p2)
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
    sub.add_parser("schema", help="dump the protocol/command model as JSON"
                   ).set_defaults(fn=cmd_schema)
    sub.add_parser("batch", help="stream JSON operations from stdin").set_defaults(fn=cmd_batch)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except Abort as exc:
        emit({"error": str(exc)}, args.json, f"error: {exc}")
        return exc.code
    except FiioError as exc:
        emit({"error": str(exc)}, args.json, f"error: {exc}")
        return EXIT_DEVICE
    except data_errors() as exc:
        # catalogue failures and bad input report the same way, so no subcommand
        # needs its own try/except; a CLI should never show a traceback either
        emit({"error": str(exc)}, args.json, f"error: {exc}")
        return EXIT_DATA
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
