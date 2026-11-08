# FiiO's online services

Two separate services sit behind `https://fiiocontrol.fiio.com`, both reachable
without an account. Recovered from the app bundle and verified against the live
endpoints.

## 1. The preset catalogue — `/ucenter-api` (encrypted)

Per-device EQ presets: FiiO's own, plus ones shared by users. This is what the
app's "handpicked" and "official recommendation" pages show.

### Request envelope

Every request body is encrypted. From the bundle (`ej`, `u6`, `tR`, `tj`, `k5`):

```
key        = 16 random bytes, hex-encoded -> a 32-CHARACTER STRING
             node-forge passes that string to AES as raw bytes, so this is
             AES-256, not AES-128. Easy detail to get wrong.
cipherText = base64( AES-256-ECB, PKCS#7, utf-8(json payload) )
cipherSign = base64( RSA PKCS#1 v1.5 encrypt(the 32-char key string) )
body       = {"cipherSign": ..., "cipherText": ...}
header     = X-Request-ID: <uuid4>
reply      = {"code": 200, "msg": "ok", "data": base64(AES-256-ECB(json))}
```

`X-Request-ID` is only client-side bookkeeping — the browser uses it to find the
AES key again when the response arrives. The server does not need it to match
anything, but it is sent for fidelity.

The RSA public key is embedded in the bundle and duplicated in
`fiioctl/cloud.py`. The bundle contains **two** keys; the second belongs to a
user-info endpoint and returns *"密文数据已损坏"* ("ciphertext corrupted") here,
which is a quick way to confirm you picked the right one.

ECB with no IV is weak, but it is what the service expects; this is
interoperability, not an endorsement.

### Endpoints

| Path | Purpose | Anonymous |
|---|---|---|
| `/search-peq` | user-shared presets for a device | yes |
| `/search-peq-official` | FiiO's own presets for a device | yes |
| `/get-peq-official-list` | paginated official list | 500s without a field we have not identified |
| `/get-peq`, `/add-peq`, `/update-peq`, `/delete-peq`, `/set-peq-isshare`, `/get-share-peq` | managing your own presets | needs a FiiO login |

Payload for both search endpoints:

```json
{"searchValue": "", "deviceType": 110}
```

`deviceType` is FiiO's product id, from the bundle's `pX` table — **110 for the
KA15**. `registry.Device.cloud_id` carries the known values. An empty
`searchValue` returns everything for that device. Passing `deviceType: 0`
returns nothing.

`searchValue` matches **preset names, not headphone model names**. Searching
"DT 177X" finds nothing even though the measurement database has that headphone,
because no one has published a preset by that name.

### Response shape

```json
{
  "id": 2,
  "styleName": "FH3 FOR KA15",
  "description": "...",
  "deviceType": 110,
  "masterGain": 8.0,
  "downloadSum": 27,
  "peqUserName": "...",
  "eqParamsJson": [
    {"position": 0, "frequency": 20, "gain": -3.6, "qValue": 1, "filterType": 0}
  ]
}
```

`filterType` uses the same enum as the device protocol, and `eqParamsJson`
arrives with the search results, so rendering a preview costs no extra request.
`masterGain` maps onto the device's EQ global gain.

Observed for the KA15: 9 official presets and 30 community ones.

## 2. The measurement database — `/ucenter-api` (plaintext)

The headphone database. These routes live under the same `/ucenter-api` prefix as
the encrypted endpoints above, but are served by a **separate axios instance with
no interceptor**, so they are plain JSON with no envelope and no account.

That detail is easy to get wrong. The app's AutoEQ page calls
`` `${Ln}/simple-list` `` where `Ln = "/measurement"`, through an instance whose
`baseURL` is `/ucenter-api` — *not* `/fasthub`, which is what the sibling
`/auto-eq` calls use. Guessing `/fasthub/measurement/simple-list` returns 404.

| Method | Path | Returns |
|---|---|---|
| GET | `/ucenter-api/measurement/simple-list` | ~4544 rows, ~380 KB |
| GET | `/ucenter-api/measurement/detail?id=N` | one measurement with `frData` |
| GET | `/ucenter-api/target/simple-list` | ~4578 target curves, ~420 KB |
| GET | `/ucenter-api/target/detail?id=N` | one target with `frData` |

```json
{"id": 714, "productName": "Beyerdynamic DT 177X Go",
 "config": "leather earpads", "source": "oratory1990"}
```

`config` is the variant discriminator — the DT 177X Go appears twice, as id 714
(leather earpads) and 715 (velour earpads). `detail` adds `rig`, `formFactor` and
`frData` as `[[hz, db], …]` (695 points for that model).

This database is **substantially larger than AutoEq's public one** and carries
models AutoEq lacks, including Drop collaborations.

## 3. The AutoEq engine — `/fasthub` (plaintext)

A FastAPI app that **publishes its own OpenAPI schema** at
`/fasthub/openapi.json`, with interactive docs at `/fasthub/docs`.

| Method | Path |
|---|---|
| GET | `/measurement/detail?id=N` |
| GET | `/measurement/fr?id=N` |
| GET | `/target/all`, `/target/detail?id=N`, `/target/fr?id=N` |
| GET | `/auto-eq/targets` |
| POST | `/auto-eq/equalize` |
| POST | `/room/room-equalize`, GET `/room/get-sweep-audio` |

`/measurement/detail?id=1` returns:

```json
{"id": 1, "product_name": "Flat", "rig": "", "form_factor": "", "source": "",
 "fr_data": [[20.0, 0.0], [20.2, 0.0], ...]}
```

`/auto-eq/equalize` is a server-side AutoEq: it takes the measurement data, a
target, and the usual AutoEq knobs (`bass_boost_gain`, `max_gain`, `max_slope`,
`treble_f_lower`, `parametric_eq_config: 8_PEAKING_WITH_SHELVES`, …) and returns
computed filters.

### Calling equalize

`POST /fasthub/auto-eq/equalize` takes the measurement and target as parallel
arrays, not ids:

```json
{
  "measurement": {"frequency": [20, 20.2, ...], "raw": [4.26, 4.29, ...]},
  "target":      {"frequency": [...], "raw": [...]},
  "name": "Beyerdynamic DT 177X Go", "source": "oratory1990", "rig": "GRAS 43AG",
  "parametric_eq": true,
  "parametric_eq_config": "8_PEAKING_WITH_SHELVES",
  "fixed_band_eq": false, "graphic_eq": false, "convolution_eq": false,
  "max_gain": 12, "max_slope": 18, "window_size": 0.08,
  "treble_f_lower": 6000, "treble_f_upper": 8000, "treble_window_size": 2,
  "bass_boost_gain": 0, "bass_boost_fc": 105, "bass_boost_q": 0.7,
  "fs": 48000, "bit_depth": 16, "f_res": 16, "phase": "minimum",
  "sound_signature": null, "sound_signature_smoothing_window_size": 1,
  "response": {"base64fp16": false, "fr_f_step": 1.02,
               "fr_fields": ["frequency", "raw", "error", "target",
                             "equalization", "equalized_raw"]}
}
```

`8_PEAKING_WITH_SHELVES` returns a low shelf, eight peaking filters and a high
shelf — **ten filters, exactly the band count most FiiO devices expose.**

The reply is `{"fr": {...}, "parametric_eq": {"fs": 48000, "filters": [...]}}`,
with each filter as `{type, fc, q, gain}` and `type` in AutoEq's vocabulary
(`PEAKING`, `LOW_SHELF`, `HIGH_SHELF`, …).

It does **not** return a pre-amp. `fiioctl` derives one locally the way AutoEq
does, by summing the filters' response with its own biquads and pulling the
result down by the peak.

### Picking a target automatically

`GET /fasthub/auto-eq/targets` returns each target with the `(source, form)`
combinations it is `recommended` or merely `compatible` for. Joining that against
`/ucenter-api/target/simple-list` by label gives the target id FiiO itself would
pick — for an oratory1990 over-ear measurement, *Harman over-ear 2018*.

## What `fiioctl` uses

- **Reads only.** Nothing publishes, edits or deletes anything on FiiO's side.
- No account, no credentials, no tokens.
- The two indexes (~800 KB together) are cached locally for 14 days, so search is
  instant and works offline. `fiioctl autoeq refresh` re-downloads them.
- Individual measurements and targets are cached indefinitely once fetched, since
  a published measurement does not change.
- The full frequency-response data is **not** mirrored: ~4500 measurements would
  be ~4500 requests and ~90 MB. They are fetched lazily, one headphone at a time.
- `cryptography` is an optional dependency (`pip install 'fiioctl[cloud]'`) and is
  only needed for the *preset* endpoints. The measurement database and the AutoEq
  engine are plaintext and need no extra dependency — but both live behind the
  same extra for simplicity.

If FiiO rotates the RSA key or changes the envelope, requests start failing with
a decryption error and `cloud.PUBLIC_KEY` needs refreshing from the bundle. The
code reports that as a plain error rather than crashing.
