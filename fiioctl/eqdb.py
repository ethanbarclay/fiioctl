"""Headphone EQ profiles.

Source is the AutoEq project, which is also the upstream of the curated lists in
FiiO's own app (their about page credits "AutoEq and FiiO Labs"). The index is a
single 850 KB markdown file, cached locally, so search works offline after the
first run.

Profiles use the ParametricEQ format shared by AutoEq, REW and FiiO Control:

    Preamp: -6.0 dB
    Filter 1: ON LSC Fc 105 Hz Gain 5.5 dB Q 0.70
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request

RAW = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results"
INDEX_URL = f"{RAW}/INDEX.md"
INDEX_TTL = 30 * 86400

#: ParametricEQ filter abbreviation -> fiioctl filter name.
TYPES = {
    "PK": "peak", "PEQ": "peak", "MODAL": "peak",
    "LSC": "low-shelf", "LS": "low-shelf", "LSQ": "low-shelf",
    "HSC": "high-shelf", "HS": "high-shelf", "HSQ": "high-shelf",
    "LP": "low-pass", "LPQ": "low-pass",
    "HP": "high-pass", "HPQ": "high-pass",
    "BP": "band-pass", "AP": "all-pass",
}

_ENTRY = re.compile(r"^- \[(?P<name>.+?)\]\((?P<path>\./[^)]+)\)(?P<tail> by .*)?$")
_FILTER = re.compile(
    r"^Filter\s+\d+:\s+(?P<state>ON|OFF)\s+(?P<type>[A-Z]+)\s+Fc\s+(?P<fc>[\d.]+)\s*Hz"
    r"\s+Gain\s+(?P<gain>-?[\d.]+)\s*dB(?:\s+Q\s+(?P<q>[\d.]+))?",
    re.I,
)
_PREAMP = re.compile(r"^Preamp:\s*(-?[\d.]+)\s*dB", re.I)


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.environ.get("LOCALAPPDATA")
    root = base or os.path.join(os.path.expanduser("~"), ".cache")
    path = os.path.join(root, "fiioctl")
    os.makedirs(path, exist_ok=True)
    return path


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "fiioctl"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def index(refresh=False):
    """Return the parsed AutoEq index, fetching it at most once per month."""
    path = os.path.join(cache_dir(), "autoeq-index.json")
    if not refresh and os.path.exists(path) and time.time() - os.path.getmtime(path) < INDEX_TTL:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    entries = []
    for line in _fetch(INDEX_URL).splitlines():
        m = _ENTRY.match(line.strip())
        if not m:
            continue
        parts = urllib.parse.unquote(m.group("path")[2:]).split("/")
        entries.append(
            {
                "name": m.group("name"),
                "path": m.group("path")[2:],
                "source": parts[0] if parts else "",
                "rig": parts[1] if len(parts) > 2 else "",
            }
        )
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh)
    return entries


#: measurers whose profiles are generally the most trusted, used only to break
#: ties between otherwise equally good matches
PREFERRED = ("oratory1990", "crinacle")


def squash(text):
    """Lowercase and drop everything that is not alphanumeric.

    This is what makes "hd650" find "Sennheiser HD 650" and "dt177x" find
    "DT 177X" - users do not type the spaces and hyphens that model names carry.
    """
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def relevance(query, name):
    """How well a name matches a query, from 4 (exactly) down to 0 (not at all)."""
    flat, name = squash(query), squash(name)
    if not flat or not name:
        return 0
    if name == flat:
        return 4
    if name.startswith(flat):
        return 3
    tokens = [squash(t) for t in query.split() if squash(t)]
    if tokens and all(t in name for t in tokens):
        return 2
    return 1 if flat in name else 0


def search(query, entries=None, limit=20):
    """Rank index entries against a free-text query, forgiving punctuation."""
    entries = entries if entries is not None else index()
    if not squash(query):
        return []

    scored = []
    for e in entries:
        score = relevance(query, e["name"])
        if not score:
            continue
        rank = 0 if e.get("source") in PREFERRED else 1
        scored.append((-score, rank, len(squash(e["name"])), e["name"], e))
    scored.sort(key=lambda row: row[:4])
    return [row[4] for row in scored[:limit]]


def profile(entry):
    """Download and parse the ParametricEQ profile for an index entry."""
    model = entry["path"].rstrip("/").split("/")[-1]
    name = urllib.parse.quote(urllib.parse.unquote(model))
    return parse(_fetch(f"{RAW}/{entry['path']}/{name}%20ParametricEQ.txt"))


def parse(text):
    """Parse ParametricEQ text into {'preamp': float, 'bands': [...]}."""
    preamp, bands = 0.0, []
    for line in text.splitlines():
        line = line.strip()
        m = _PREAMP.match(line)
        if m:
            preamp = float(m.group(1))
            continue
        m = _FILTER.match(line)
        if not m or m.group("state").upper() == "OFF":
            continue
        bands.append(
            {
                "freq": int(round(float(m.group("fc")))),
                "gain": float(m.group("gain")),
                "q": float(m.group("q") or 0.7),
                "filter": TYPES.get(m.group("type").upper(), "peak"),
            }
        )
    if not bands:
        raise ValueError("no enabled filters found in profile")
    return {"preamp": preamp, "bands": bands}


def render(preamp, bands):
    """Serialise back to ParametricEQ text (for `fiioctl peq export`)."""
    abbrev = {v: k for k, v in TYPES.items() if k in ("PK", "LSC", "HSC", "LP", "HP", "BP", "AP")}
    lines = [f"Preamp: {preamp:.1f} dB"]
    for i, b in enumerate(bands, 1):
        kind = abbrev.get(b.get("filter", "peak"), "PK")
        lines.append(
            f"Filter {i}: ON {kind} Fc {int(round(b['freq']))} Hz "
            f"Gain {b['gain']:.1f} dB Q {b['q']:.2f}"
        )
    return "\n".join(lines) + "\n"
