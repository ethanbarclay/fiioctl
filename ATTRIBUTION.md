# Attribution and licensing

`fiioctl` is MIT licensed (see [LICENSE](LICENSE)). It contains no third-party
source code: every line is original Python. What it *does* owe to others is
knowledge and data, credited below.

## Protocol knowledge

The HID protocol in [docs/PROTOCOL.md](docs/PROTOCOL.md) was obtained by
analysing the **FiiO Control** web application, as vendored for offline use by
[Ircama/fiiocontrol](https://github.com/Ircama/fiiocontrol), and then verifying
the findings against real hardware.

- That repository carries **no licence file**, and the application it packages is
  FiiO's own proprietary software. Its bundled credits page states *"All rights
  of interpretation belong to FiiO."*
- Nothing was copied from it. Command IDs, byte layouts, fixed-point scalings,
  report IDs and command-pacing intervals are **factual observations** about how
  a device communicates, re-expressed in an independent implementation. Where it
  aids verification, `docs/PROTOCOL.md` cites the bundle's minified symbol names
  (`zt`, `SX`, `l3e`, `w3e`) so a reader can confirm the findings for themselves.
- Every protocol fact used here was additionally confirmed on a FiiO KA15
  (firmware 1.1.6), and the observations that came only from hardware are marked
  as such in `docs/PROTOCOL.md`.
- **FiiO**, **JadeAudio** and **Snowsky** are trademarks of Guangzhou FiiO
  Electronics Technology Co., Ltd. This project is not affiliated with,
  authorised by, or endorsed by FiiO.

[SmookeyDev/fiio-k13-control](https://github.com/SmookeyDev/fiio-k13-control)
(MIT) was useful as a second, independent confirmation that the same GET/SET
framing and per-model report-ID scheme appear on another FiiO device family. Only
its prose documentation was read; no code was copied, linked, or adapted.

## Headphone measurement data

Headphone profiles come from **[AutoEq](https://github.com/jaakkopasanen/AutoEq)**
by Jaakko Pasanen, MIT licensed. FiiO's own app credits the same project as the
origin of its measurement data.

`fiioctl` **does not redistribute** any of it. The index and the individual
`ParametricEQ.txt` profiles are fetched from AutoEq at runtime and cached in the
user's own cache directory.

The measurements themselves were taken by independent contributors. AutoEq lists
them, and so does FiiO's app:

> crinacle · oratory1990 · Innerfidelity · Rtings · Super Review · ToneDeafMonk ·
> Kuulokenurkka · Hi End Portable · Regan Cipher · Jaytiss · Headphone.com
> (legacy) · Filk · Harpo · Kr0mka · RikudouGoku · Fahryst · DHRME · Kazi ·
> freeryder05 · Auriculares Argentina · Bakkwatan · Ted's Squig Hoard · FiiO

`fiioctl` shows the measurer's name beside every search result and in `--json`
output, so attribution travels with the data rather than being stripped at the
point of use. Several contributors publish on personal-use terms; if you intend
to redistribute profiles or use them commercially, check the individual
measurer's conditions. AutoEq's own README does not impose attribution or
non-commercial terms, but it does not grant rights over the underlying
measurements either.

## FiiO's online preset catalogue

With the optional `cloud` extra, `fiioctl` reads FiiO's own preset endpoints at
`fiiocontrol.fiio.com` — the same anonymous, read-only requests their web client
makes. See [docs/CATALOGUE.md](docs/CATALOGUE.md).

- **Read-only.** Nothing is published, edited or deleted. The endpoints that
  would require a FiiO account are deliberately not implemented.
- The presets themselves are authored by **FiiO** and by **individual users** who
  chose to share them. `fiioctl` shows the publisher's name and the
  official/community distinction on every row, so it is clear whose tuning is
  being applied.
- Preset content is not redistributed with this project; it is fetched on demand.
- The request envelope is reimplemented from the bundle (AES-256-ECB payload with
  an RSA-wrapped key). The RSA key in `fiioctl/cloud.py` is a **public** key
  taken from the shipped application, not a secret.

This is interoperability with a public endpoint, not an endorsement of its design
(ECB without an IV is weak) and not a grant of rights over the preset data.

## Runtime dependencies

| Package | Licence | Note |
|---|---|---|
| [cython-hidapi](https://github.com/trezor/cython-hidapi) (`hidapi`) | **Tri-licensed: GPL-3.0, BSD-style, or the original hidapi licence — the choice is the user's** | Required. If you redistribute a bundled binary, pick a licence deliberately; the GPL option would be viral. |
| [Textual](https://github.com/Textualize/textual) | MIT | Optional, only for `fiioctl ui` |

`fiioctl` imports `hid` at runtime and does not vendor or statically link it, so
installing from source leaves the licence choice entirely with you.

## Summary

- Original code, MIT.
- Protocol: independently reimplemented facts, verified on hardware, sources
  cited, nothing copied.
- Measurement data: fetched at runtime from AutoEq, never redistributed, measurer
  credited in the UI.
- Not affiliated with FiiO.
