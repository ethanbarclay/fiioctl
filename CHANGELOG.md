# Changelog

Notable changes per release. Versions follow [semantic versioning](https://semver.org),
with the caveat that a 0.x line can break its CLI surface between minor versions
if a protocol finding demands it.

## 0.1.0 - 2026-11-08

First release. Everything below was reverse-engineered from the FiiO Control web
app's bundle and then checked against real hardware where a device was available
to check it with.

### Added

- **Wire protocol.** HID framing, the fixed-point codecs for gain, frequency and
  Q, the filter enum, and the ten-band parametric EQ, documented in
  `docs/PROTOCOL.md` with each finding's source labelled.
- **Device layer.** Interface selection that prefers the vendor usage page,
  request pacing, adaptive write length, and a transport gate so models that
  speak a different protocol refuse rather than misbehave.
- **EQ control.** Per-band set and get, global gain with headroom-safe rounding,
  the ten preset slots, reading and renaming the three user slots, and saving
  the live curve into a slot.
- **Onboard settings.** Volume, channel balance, the digital filter, screen
  language and screen brightness.
- **Curve preview.** RBJ biquad magnitude response plotted in the terminal, so a
  profile can be judged before it is written to the device.
- **AutoEq search.** Index download with a 14-day cache, punctuation-insensitive
  matching, profile parsing, and `autoeq refresh`.
- **FiiO catalogue.** The encrypted `/ucenter-api` envelope, preset and
  measurement search, target lists and compatibility, and server-side AutoEq.
  Reads work without an account; publishing presets is deliberately not
  implemented.
- **Terminal UI** (`fiioctl ui`, `ui` extra): four pages covering the live EQ
  with curve, the slot manager, headphone search with measurement previews, and
  onboard settings.
- **Scripting surface.** `--json` on every command, stable exit codes,
  `fiioctl schema` dumping the whole model as one document, and `fiioctl batch`
  for many operations in a row.
- **Device profiles.** Twenty-one models, of which the KA15 is hardware-verified;
  `fiioctl scan` and the device-report issue template exist to grow that list.
- **Linux packaging.** A udev rule in `packaging/` so the device is reachable
  without root.

### Verified on hardware

A FiiO KA15 on firmware 1.1.6. Three bugs were found that reading the code had
not: global gain silently losing headroom, because the device stores whole
decibels and truncates toward zero; preset names reading back as garbage,
because the firmware never clears the eight-byte field; and two settings
(language, brightness) that the web app does not expose at all.

### Not included

Firmware flashing. The protocol for it is understood but writing to flash is a
different risk class, and it is held for a later release.
