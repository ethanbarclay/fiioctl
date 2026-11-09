"""Every place a headphone EQ profile can come from, as one search.

    FiiO's preset catalogue   per model; results already carry their bands
    FiiO's measurements       computed into bands by FiiO's AutoEq service
    the AutoEq project        a ready-made ParametricEQ file per headphone

`search` merges them and `resolve` fills in whatever a result still needs, so the
CLI and the TUI offer the same results and neither has to care which is which.
"""

from urllib.parse import quote, unquote

from . import cloud, eqdb

DEFAULT_LIMIT = 50

OFFICIAL, COMMUNITY, MEASURED, AUTOEQ = "fiio-official", "fiio-community", "fiio-autoeq", "autoeq"

#: which source goes first when results match a query equally well
TRUST = {OFFICIAL: 0, AUTOEQ: 1, MEASURED: 2, COMMUNITY: 3}


def _preset(profile):
    # the name is part of the id because searching for it is the only way back to
    # a preset: FiiO has no lookup by id, and an empty search stops at 30 results
    name = quote(profile["name"], safe="")
    return {**profile, "id": f"{profile['source']}:{profile['id']}:{name}"}


def _measurement(row):
    variant = f" ({row['config']})" if row.get("config") else ""
    return {"id": f"{MEASURED}:{row['id']}", "name": f"{row['productName']}{variant}",
            "source": MEASURED, "rig": row.get("source") or "",
            "bands": None, "meas_id": row["id"]}


def _autoeq(entry):
    rig = " ".join(part for part in (entry.get("source"), entry.get("rig")) if part)
    return {"id": f"{AUTOEQ}:{entry['path']}", "name": entry["name"], "source": AUTOEQ,
            "rig": rig, "bands": None, "entry": entry}


def search(query, cloud_id=None, limit=DEFAULT_LIMIT):
    """Search every source. Returns (results, best first; problems, as text).

    FiiO's presets are per model, so they need `cloud_id`. A source that fails
    becomes a problem rather than an exception: the others are still worth having.
    """
    found, problems = [], []

    def gather(label, fetch):
        try:
            found.extend(fetch())
        except Exception as exc:  # noqa: BLE001 - one failing source is non-fatal
            problems.append(f"{label}: {exc}")

    if cloud_id:
        gather("FiiO presets",
               lambda: [_preset(p) for p in cloud.presets(cloud_id, query)[:limit]])
    gather("FiiO measurements",
           lambda: [_measurement(r) for r in cloud.find_measurements(query, limit=limit)])
    gather("AutoEq", lambda: [_autoeq(e) for e in eqdb.search(query, limit=limit)])
    # Community presets always go last, however well they match: the top result
    # is what `eq apply` writes by default, and anyone can publish one. The sort
    # is stable, so each source's own ranking survives within a tie.
    found.sort(key=lambda r: (r["source"] == COMMUNITY, -eqdb.relevance(query, r["name"]),
                              TRUST[r["source"]]))
    return found[:limit], problems


def lookup(text, cloud_id=None):
    """The result a `search` id names, or None if `text` is not an id."""
    source, _, key = text.partition(":")
    if source not in TRUST or not key:
        return None
    if source == AUTOEQ:
        parts = unquote(key).split("/")
        return _autoeq({"name": parts[-1], "path": key, "source": parts[0],
                        "rig": parts[1] if len(parts) > 2 else ""})
    if source == MEASURED:
        if not key.isdigit():
            return None
        return _measurement({"id": int(key), "productName": f"measurement {key}"})
    number, _, name = key.partition(":")
    for profile in cloud.presets(cloud_id, unquote(name), official=source == OFFICIAL,
                                 community=source == COMMUNITY):
        if str(profile["id"]) == number:
            return _preset(profile)
    raise cloud.CloudError(f"no preset {text} for this device")


def resolve(result, count):
    """Fill in a result's bands and preamp for a device with `count` bands.

    May hit the network, so the TUI calls this from a worker.
    """
    if result.get("bands") is not None:
        return result
    if result.get("meas_id") is not None:
        profile = cloud.auto_eq(result["meas_id"], bands=count)
        result.update(name=profile["name"], bands=profile["bands"], preamp=profile["preamp"],
                      description=profile["description"])
    else:
        profile = eqdb.profile(result["entry"])
        result.update(bands=profile["bands"], preamp=profile["preamp"])
    return result
