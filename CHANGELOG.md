# Changelog

Notable changes per release. Versions follow [semantic versioning](https://semver.org),
with the caveat that a 0.x line can break its CLI surface between minor versions
if a protocol finding demands it.

## 0.2.1 - 2026-11-08

Linux fixes, found by running on Linux for the first time. If you installed
`99-fiio.rules`, replace it with `70-fiio.rules` as the README describes.

### Fixed

- The Linux udev rule is now `70-fiio.rules`. Named `99-fiio.rules`, it ran after
  systemd's `73-seat-late.rules`, so `uaccess` never granted the logged-in user
  access. Anyone who installed the old file should delete it and install this one.
- Linux now uses hidapi's hidraw backend. The default libusb one detached the
  kernel's HID driver on every command, could not read a device's name without
  permission, and reported no usage page, so the control interface could not be
  picked by it.
- Programs using the same device on Linux take turns, so one cannot read another's
  reply as its own or send a write too soon after another's.
- A device that will not open is no longer always blamed on the udev rule. On Linux
  the error says whether the device has gone or the user has no access to it.

### Verified on hardware

A FiiO KA15 on firmware 1.1.6, on CachyOS: the rule tested as `99-` and `70-` and
installed through a real re-plug, every read and reversible write, all three
`eq apply` sources, the TUI, HID commands during an audio stream, and programs
overlapping on the same device. No model other than the KA15 has been tried on
Linux.

### Not verified

A model with several HID interfaces on hidraw, saving to a user slot, the fallback
to libusb, and a failed write falling through to the next length.

## 0.2.0 - 2026-11-08

Failed writes are now noticed, values a setting cannot hold are refused, the TUI
talks to the device strictly in order, and `eq search` covers every profile
source. Some CLI output changes, listed below, can break scripts.

### Changed

- `--trace` and `--dry-run` packet logs go to stderr rather than stdout.
- `eq search` now covers everything the TUI's library does: FiiO's presets for
  the model, FiiO's measurements and AutoEq, merged and ranked the same way in
  both. `eq apply` applies a result from any of them, by search or by id. Its
  `--json` rows gain `id` and `index`, and `source` now names the source.
  Community presets always rank last, and `--limit` caps the merged list,
  defaulting to 50.
- `autoeq search` output uses a plain hyphen rather than an em dash.
- The refusal for the seven adapter models no longer says they use a different
  protocol. FiiO's app sends them the same HID commands; they are refused because
  nobody has tested one.

### Fixed

- Failed HID writes are detected, so the write-length fallback runs and a dead
  device raises an error instead of reporting success.
- Values a setting cannot hold are refused rather than wrapped onto the wire:
  `set volume -1` was sent as 0xFF. The same goes for a band's index, frequency
  and Q.
- Unrecognised on/off values are refused rather than turning the setting off.
- Only the listed products of the rebadged controller vendors are treated as
  FiiO devices, in device selection and in the udev rules.
- The TUI rounds a profile's pre-amp downward like the CLI, shows the new EQ
  after applying it, and reloads bands whenever the preset changes.
- TUI device writes happen strictly in the order they were made, and edits are
  refused while a reload, preset switch or profile apply is pending.
- After applying a profile, the TUI shows the values the device was sent rather
  than the profile's unrounded ones.
- `eq save` and `--save` refuse a factory slot, on models whose user slots are
  known, and before anything is written.
- A device whose name cannot be read says what was found and points at the udev
  rule, rather than reporting `cannot open ''`.
- A `batch` line that is not a JSON object is reported instead of crashing.
- `autoeq` picks the recommended target for the measurement's rig as well as its
  source and form factor.
- `--json` is accepted after the subcommand as well as before it.

### Verified on hardware

A FiiO KA15 on firmware 1.1.6, through both the CLI and the TUI: writes with
read-back, preset switches, edits landing in order, undo, and applying an official
preset, an AutoEq result and a FiiO measurement. It found that the TUI was showing
a profile's unrounded values, that Q drifts by up to about 0.02 when written, and
that unsaved band edits survive a preset switch.

### Not verified

Saving to a user slot, the error for a device with no readable name, and a failed
write falling through to the next length. No model other than the KA15 has been
tried, including the adapter models.

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
- **AutoEq search.** Index download with a 30-day cache, punctuation-insensitive
  matching, profile parsing, and `autoeq refresh`.
- **FiiO catalogue.** The encrypted `/ucenter-api` envelope, preset and
  measurement search, target lists and compatibility, and server-side AutoEq.
  The two indexes are cached for 14 days and curves are fetched lazily. Reads
  work without an account; publishing presets is deliberately not implemented.
- **Terminal UI** (`fiioctl ui`, `ui` extra): four pages covering the live EQ
  with curve, the slot manager, headphone search with measurement previews, and
  onboard settings.
- **Scripting surface.** `--json` on every command, stable exit codes,
  `fiioctl schema` dumping the whole model as one document, and `fiioctl batch`
  for many operations in a row.
- **Device profiles.** Twenty-one models, of which the KA15 is
  hardware-verified; `fiioctl scan` and the device-report issue template exist
  to grow that list.
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
