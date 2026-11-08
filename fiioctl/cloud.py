"""FiiO's online preset catalogue.

Optional: needs `pip install 'fiioctl[cloud]'` for the `cryptography` dependency.

The official app talks to `https://fiiocontrol.fiio.com/ucenter-api` with an
encrypted envelope, recovered from its bundle:

    key        = 16 random bytes, hex-encoded to a 32-CHARACTER STRING
                 (node-forge treats that string as 32 raw bytes, so AES-256)
    cipherText = base64(AES-256-ECB, PKCS#7, utf-8 json)
    cipherSign = base64(RSA PKCS#1 v1.5 encrypt(the 32-char key string))
    body       = {"cipherSign": ..., "cipherText": ...}
    reply      = {"code": 200, "data": base64(AES-256-ECB(json))}

Reads work without an account. Everything here is read-only; publishing presets
needs a FiiO login and is deliberately not implemented.
"""

import base64
import json
import os
import secrets
import time
import urllib.error
import urllib.request
import uuid

from . import proto

BASE = "https://fiiocontrol.fiio.com/ucenter-api"
FASTHUB = "https://fiiocontrol.fiio.com/fasthub"
TIMEOUT = 25

#: The public key shipped in the app bundle. If FiiO rotates it, requests start
#: failing with "ciphertext corrupted" and this needs refreshing from the bundle.
PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0mPGrgZPDCPDd9m129qy
+TV5HeLWDTJbNK5wlUAemqH3NyrOh3HUo+LbTbMf6J45AFDjNRCDiZZc3Y+uCici
9fHn0dixbtuHOdJer0U/4xHloOgYmsTwhAh56njWQAyaqoi0R3nG0bpCgsi5omlS
RqP5KyaybuKjPyZvGVn0IKnVVQx3AI1+p/5lWARv23nPNS9ehfKts5oeFdOAKgT2
mvV80TzJHbc2mKU8XoBr0VgX4Ohgq/A+Ddv0Wz0bNAQJgzrFAN1lIg2NqktcGdzD
/HHPIeGCYG2gB7ZADsGX6vGpSoeUj3anr25nojQVbZgEBeE/6nJZ793Qhq8vudep
XQIDAQAB
-----END PUBLIC KEY-----"""


class CloudError(RuntimeError):
    """Catalogue unreachable, or the protocol changed."""


def _crypto():
    try:
        from cryptography.hazmat.primitives import padding, serialization
        from cryptography.hazmat.primitives.asymmetric import padding as apad
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise CloudError(
            "the FiiO catalogue needs the 'cryptography' package: "
            "pip install 'fiioctl[cloud]'"
        ) from exc
    return padding, serialization, apad, Cipher, algorithms, modes


def _post(path, payload):
    padding, serialization, apad, Cipher, algorithms, modes = _crypto()
    key = secrets.token_hex(16).encode("ascii")  # 32 ASCII bytes -> AES-256

    padder = padding.PKCS7(128).padder()
    blob = padder.update(json.dumps(payload).encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    cipher_text = base64.b64encode(enc.update(blob) + enc.finalize()).decode()

    pub = serialization.load_pem_public_key(PUBLIC_KEY.encode())
    cipher_sign = base64.b64encode(pub.encrypt(key, apad.PKCS1v15())).decode()

    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps({"cipherSign": cipher_sign, "cipherText": cipher_text}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Request-ID": str(uuid.uuid4()),
            "User-Agent": "fiioctl",
            "Referer": "https://fiiocontrol.fiio.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            outer = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise CloudError(f"catalogue returned HTTP {exc.code}") from exc
    except OSError as exc:
        raise CloudError(f"catalogue unreachable: {exc}") from exc

    if outer.get("code") != 200:
        raise CloudError(f"catalogue error: {outer.get('msg') or outer.get('code')}")
    data = outer.get("data")
    if isinstance(data, str):
        dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        raw = dec.update(base64.b64decode(data)) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        data = json.loads((unpadder.update(raw) + unpadder.finalize()).decode("utf-8"))
    return data or []


def _as_profile(row, source):
    """Convert a catalogue row into the same shape eqdb.parse produces."""
    bands = []
    for band in row.get("eqParamsJson") or []:
        kind = band.get("filterType", 0)
        bands.append({
            "freq": int(round(float(band.get("frequency", 1000)))),
            "gain": float(band.get("gain", 0.0)),
            "q": float(band.get("qValue", 0.7) or 0.7),
            "filter": proto.FILTER_NAMES.get(kind, "peak"),
        })
    bands.sort(key=lambda b: b.get("position", 0) if isinstance(b, dict) else 0)
    return {
        "name": row.get("styleName") or row.get("deviceName") or f"preset {row.get('id')}",
        "source": source,
        "rig": row.get("peqUserName") or "",
        "description": (row.get("description") or "").strip(),
        "downloads": row.get("downloadSum") or 0,
        "preamp": float(row.get("masterGain") or 0.0),
        "bands": bands,
        "id": row.get("id"),
    }


def presets(device_type, query="", official=True, community=True):
    """Presets published for one device model, newest-looking first.

    Each result already carries its bands, so a preview costs no extra request.
    """
    if not device_type:
        raise CloudError("this device has no known FiiO catalogue id")
    out = []
    if official:
        for row in _post("/search-peq-official",
                         {"searchValue": query, "deviceType": device_type}):
            out.append(_as_profile(row, "fiio-official"))
    if community:
        for row in _post("/search-peq", {"searchValue": query, "deviceType": device_type}):
            out.append(_as_profile(row, "fiio-community"))
    return [p for p in out if p["bands"]]


# -- the measurement database ------------------------------------------------
#
# Plaintext JSON, no account. The index lives under /ucenter-api (despite that
# prefix carrying the encrypted endpoints too - these particular routes are
# served by an axios instance with no interceptor), while the AutoEq engine that
# turns a measurement into filters lives under /fasthub.

INDEX_TTL = 14 * 86400


def _get(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "fiioctl", "Accept": "application/json",
                      "Referer": "https://fiiocontrol.fiio.com/equalizer/auto-eq"})
    try:
        with urllib.request.urlopen(request, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise CloudError(f"FiiO returned HTTP {exc.code} for {url}") from exc
    except OSError as exc:
        raise CloudError(f"FiiO unreachable: {exc}") from exc


def _cached(name, url, refresh=False, ttl=INDEX_TTL):
    """Fetch a JSON document, keeping a copy on disk."""
    from . import eqdb  # local import: shares the cache directory
    path = os.path.join(eqdb.cache_dir(), name)
    fresh = ttl is None or (os.path.exists(path)
                            and time.time() - os.path.getmtime(path) < ttl)
    if not refresh and os.path.exists(path) and fresh:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    data = _get(url)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return data


def measurements(refresh=False):
    """Every headphone measurement FiiO knows: {id, productName, config, source}.

    ~4500 entries, ~380 KB, cached locally so search is instant and offline.
    """
    return _cached("fiio-measurements.json",
                   f"{BASE}/measurement/simple-list", refresh)


def target_list(refresh=False):
    """Every target curve: {id, label, bassBoost}."""
    return _cached("fiio-targets.json", f"{BASE}/target/simple-list", refresh)


def target_compat(refresh=False):
    """AutoEq target definitions with their compatible source/form combinations."""
    return _cached("fiio-target-compat.json", f"{FASTHUB}/auto-eq/targets", refresh)


def measurement_detail(measurement_id):
    """One measurement including its frequency response (cached indefinitely)."""
    return _cached(f"fiio-m-{int(measurement_id)}.json",
                   f"{BASE}/measurement/detail?id={int(measurement_id)}", ttl=None)


def target_detail(target_id):
    """One target curve including its frequency response (cached indefinitely)."""
    return _cached(f"fiio-t-{int(target_id)}.json",
                   f"{BASE}/target/detail?id={int(target_id)}", ttl=None)


def refresh_indexes():
    """Re-download the measurement and target indexes. Returns their sizes."""
    return {
        "measurements": len(measurements(refresh=True)),
        "targets": len(target_list(refresh=True)),
        "target_compatibility": len(target_compat(refresh=True)),
    }


def find_measurements(query, limit=25, entries=None):
    """Search the measurement index, forgiving punctuation and spacing."""
    from .eqdb import squash
    rows = entries if entries is not None else measurements()
    flat = squash(query)
    if not flat:
        return []
    tokens = [squash(t) for t in query.split() if squash(t)]
    scored = []
    for row in rows:
        name = squash(row.get("productName") or "")
        if not name:
            continue
        if name == flat:
            score = 4
        elif name.startswith(flat):
            score = 3
        elif tokens and all(t in name for t in tokens):
            score = 2
        elif flat in name:
            score = 1
        else:
            continue
        scored.append((-score, len(name), row.get("productName") or "", row))
    scored.sort(key=lambda r: r[:3])
    return [r[3] for r in scored[:limit]]


def recommended_target(measurement, targets_by_label=None, compat=None):
    """Pick the target FiiO itself recommends for this measurement's rig and form."""
    labels = targets_by_label or {t["label"]: t["id"] for t in target_list()}
    source = (measurement.get("source") or "").lower()
    form = (measurement.get("formFactor") or "").lower()

    def matches(entries):
        for entry in entries or []:
            if (entry.get("source") or "").lower() == source and \
                    (entry.get("form") or "").lower() == form:
                return True
        return False

    for key in ("recommended", "compatible"):
        for row in compat if compat is not None else target_compat():
            if matches(row.get(key)) and row.get("label") in labels:
                return labels[row["label"]], row["label"]
    # Nothing declared: fall back to the usual Harman curve for the form factor.
    fallback = "Harman in-ear 2019" if "in-ear" in form else "Harman over-ear 2018"
    if fallback in labels:
        return labels[fallback], fallback
    first = next(iter(labels.items()))
    return first[1], first[0]


#: AutoEq filter-set names. "8 peaking with shelves" yields a low shelf, eight
#: peaking filters and a high shelf - ten in total, which is what most FiiO
#: devices expose.
EQ_CONFIGS = {
    10: "8_PEAKING_WITH_SHELVES",
    8: "8_PEAKING_WITH_SHELVES",
    5: "4_PEAKING_WITH_LOW_SHELF",
}

AUTOEQ_DEFAULTS = {
    "bass_boost_gain": 0, "bass_boost_fc": 105, "bass_boost_q": 0.7,
    "treble_boost_gain": 0, "treble_boost_fc": 10000, "treble_boost_q": 0.7,
    "tilt": 0, "fs": 48000, "bit_depth": 16, "f_res": 16, "phase": "minimum",
    "sound_signature": None, "sound_signature_smoothing_window_size": 1,
    "max_gain": 12, "max_slope": 18, "window_size": 0.08,
    "treble_window_size": 2, "treble_f_lower": 6000, "treble_f_upper": 8000,
    "treble_gain_k": 1, "preamp": 0,
}

_TYPE_NAMES = {
    "PEAKING": "peak", "LOW_SHELF": "low-shelf", "HIGH_SHELF": "high-shelf",
    "BAND_PASS": "band-pass", "LOW_PASS": "low-pass", "HIGH_PASS": "high-pass",
    "ALL_PASS": "all-pass",
}


def _split(fr):
    """[[freq, db], ...] -> {"frequency": [...], "raw": [...]}, as the app sends it."""
    return {"frequency": [p[0] for p in fr], "raw": [p[1] for p in fr]}


def auto_eq(measurement_id, target_id=None, bands=10, **overrides):
    """Run FiiO's AutoEq for one headphone and return a profile ready to apply.

    Same service the web app's AutoEQ page uses, so the result matches what the
    official UI would produce for the same measurement and target.
    """
    meas = measurement_detail(measurement_id)
    if not meas.get("frData"):
        raise CloudError(f"measurement {measurement_id} has no frequency response")
    if target_id is None:
        target_id, _ = recommended_target(meas)
    target = target_detail(target_id)
    tfr = target.get("frData") or target.get("fr") or []
    if not tfr:
        raise CloudError(f"target {target_id} has no curve")

    payload = dict(AUTOEQ_DEFAULTS)
    payload.update(overrides)
    payload.update({
        "measurement": _split(meas["frData"]),
        "name": meas.get("productName"),
        "source": meas.get("source"),
        "rig": meas.get("rig"),
        "target": _split(tfr),
        "parametric_eq": True,
        "parametric_eq_config": EQ_CONFIGS.get(bands, EQ_CONFIGS[10]),
        "fixed_band_eq": False, "graphic_eq": False, "convolution_eq": False,
        "response": {"base64fp16": False, "fr_f_step": 1.02,
                     "fr_fields": ["frequency", "raw", "error", "target",
                                   "equalization", "equalized_raw"]},
    })

    request = urllib.request.Request(
        f"{FASTHUB}/auto-eq/equalize", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "fiioctl",
                 "Referer": "https://fiiocontrol.fiio.com/equalizer/auto-eq"})
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            out = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise CloudError(f"equalize failed: HTTP {exc.code} "
                         f"{exc.read()[:200].decode('utf-8', 'replace')}") from exc
    except OSError as exc:
        raise CloudError(f"equalize unreachable: {exc}") from exc

    filters = (out.get("parametric_eq") or {}).get("filters") or []
    if not filters:
        raise CloudError("equalize returned no filters")

    out_bands = [{
        "freq": int(round(f["fc"])),
        "gain": round(float(f["gain"]), 1),
        "q": round(float(f["q"]), 2),
        "filter": _TYPE_NAMES.get(f.get("type", "PEAKING"), "peak"),
    } for f in filters[:bands]]

    # The service does not return a pre-amp, so derive one the way AutoEq does:
    # pull everything down by the peak of the resulting curve.
    from .curve import response
    peak = max(response(out_bands)[1], default=0.0)
    name = meas.get("productName") or f"measurement {measurement_id}"
    if meas.get("config"):
        name = f"{name} ({meas['config']})"
    return {
        "name": name,
        "source": f"fiio-autoeq/{meas.get('source') or '?'}",
        "rig": meas.get("rig") or "",
        "description": f"target: {target.get('label', target_id)}",
        "downloads": 0,
        "preamp": -round(max(0.0, peak), 1),
        "bands": out_bands,
        "id": measurement_id,
        "target_id": target_id,
        "target_label": target.get("label"),
    }
