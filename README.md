# fiioctl

[![CI](https://github.com/ethanbarclay/fiioctl/actions/workflows/ci.yml/badge.svg)](https://github.com/ethanbarclay/fiioctl/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/fiioctl)](https://pypi.org/project/fiioctl/)
[![Python versions](https://img.shields.io/pypi/pyversions/fiioctl)](https://pypi.org/project/fiioctl/)

Control FiiO USB DACs from the command line on Linux, macOS and Windows. A small,
declarative reimplementation of what the FiiO Control web app does over WebHID,
built to be scripted.

The CLI core is ~1500 lines of Python with a single runtime dependency
(`hidapi`) and no daemon. The terminal UI and FiiO-catalogue support are optional
extras on top.

Session output below is illustrative; see
[Verification status](#verification-status) for what has actually been run.

```console
$ fiioctl info
product            FIIO KA15
matched_profile    FIIO KA15
report_id          7
firmware           1.14
bands              10
capabilities       eq, peq, preset-names, volume, filter, balance

$ fiioctl eq search "hd 650"
 0  Sennheiser HD 650  [autoeq / oratory1990 over-ear]
 1  Sennheiser HD 650  [autoeq / crinacle GRAS 43AG-7 over-ear]
 2  Sennheiser HD 650  [autoeq / Kuulokenurkka over-ear]

$ fiioctl eq apply "hd 650" --slot user1 --save
applied 'Sennheiser HD 650' (autoeq): 10 bands, preamp -5.8 dB

$ fiioctl peq show
global gain   -5.8 dB
   0     105 Hz    +5.5 dB  Q  0.70  low-shelf
   1     180 Hz    -3.3 dB  Q  0.57  peak
   ...
```

## Install

```bash
pipx install fiioctl                 # or: pip install fiioctl
pipx install 'fiioctl[all]'          # with the terminal UI and catalogue search
```

From a clone instead, for hacking on it:

```bash
git clone https://github.com/ethanbarclay/fiioctl && cd fiioctl
pip install -e '.[dev]'
```

The one dependency is **`hidapi`** (cython-hidapi), whose wheels bundle the
hidapi library, so there is nothing to install by hand on any platform.

> **Watch out for the `hid` package.** A *different* PyPI distribution named
> `hid` also installs a top-level `hid` module, with an incompatible API and no
> bundled library. Its package directory shadows cython-hidapi's module, and you
> get `ImportError: Unable to load any of the following libraries`. If you hit
> that: `pip uninstall hid`.

If you use **pyenv-win**, console scripts land in the version's `Scripts`
directory, which is not on `PATH`, so run `pyenv rehash` once after installing to
generate the shim, otherwise `fiioctl` is "not recognized".

On Linux, allow non-root access to the device. The rule is not part of the
installed package, so fetch it:

```bash
curl -sO https://raw.githubusercontent.com/ethanbarclay/fiioctl/main/packaging/99-fiio.rules
sudo install -m644 99-fiio.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

From a clone, the file is already there as `packaging/99-fiio.rules`.

Then re-plug the DAC. On macOS and Windows no setup is needed.

## What it does

| Area | Commands |
|---|---|
| Discovery | `fiioctl devices`, `fiioctl info`, `fiioctl state` |
| Any setting | `fiioctl get <name>`, `fiioctl set <name> <value>` |
| EQ presets | `fiioctl eq preset [name]`, `eq name <slot> [NAME]`, `eq save [slot]` |
| Headphone EQ | `fiioctl eq search <query>`, `fiioctl eq apply <query> [--slot] [--save]` |
| Parametric EQ | `fiioctl peq show`, `peq set <band> --freq --gain --q --filter`, `peq import/export`, `peq clear` |
| FiiO catalogue | `fiioctl cloud list`, `cloud show <n>`, `cloud apply <n>` |
| Terminal UI | `fiioctl ui` |
| Escape hatch | `fiioctl raw <cmd> [hex] [--read]`, global `--trace` |
| Machine use | `fiioctl schema`, `fiioctl batch`, `--json` on everything |

`fiioctl get`/`set` cover every decoded command: volume, volume limit, line-out
switch, channel balance, DAC filter, EQ master switch, EQ global gain, indicator
LED, screen orientation, and the microphone controls on devices that have one.
Run `fiioctl set --help` for the current list.

## Terminal UI

```bash
pip install 'fiioctl[all]'
fiioctl ui
```

Four pages down the left (**Device**, **Equaliser**, **EQ slots**, **Library**),
switched with `1`–`4`. One rule everywhere: **arrow to a thing, press `+` or `-`
to change it.** No buttons, no forms to submit, and the hint line at the bottom
always names what is selected and what the step is, so there is nothing to
memorise.

```
 1  Device       │ #  frequency  gain     Q     filter    global gain -7.0 dB
 2  Equaliser    │ 0  32 Hz      +3.0 dB  0.71  peak        +6 ┤     ━━━
 3  EQ slots     │ 1  64 Hz      +3.0 dB  0.71  peak           ┤  ━━━   ━━━
 4  Library      │ 2  125 Hz     +2.5 dB  0.71  peak           ┤━━         ━         ━    ━━━
                 │ 3  250 Hz     -0.5 dB  0.71  peak         0 ┼────────────━───━━─────━━━━━━━
                 │ 4  500 Hz     -2.5 dB  0.71  peak           ┤             ━━━━━
 ─────────────────────────────────────────────────────────────────────────────────────────────
 selected  band 2 · gain = +2.5 dB
 + / - change by 0.5 dB   PgUp / PgDn by 5 dB   Tab switch   1-4 pages   ? keys
```

- **Device.** Every onboard setting the model exposes: volume, volume limit,
  line-out switch, channel balance, DAC filter, EQ preset, global gain, and the
  LED/screen/microphone controls on devices that have them.
- **Equaliser.** The band table with a live response curve, computed from the
  same RBJ biquads the official app uses.
- **EQ slots.** Every preset slot with its stored name and which one is active.
  `Enter` loads one, `n` renames a user slot, `s` saves the current EQ into it.
- **Library.** Search AutoEq *and* FiiO's catalogue together, with a curve
  preview for the highlighted result before you commit it.

`u` undoes the last write, `r` re-reads from the device, `?` lists every key.

## FiiO's own preset catalogue

`fiioctl cloud` reads the presets FiiO and other users publish for your exact
model, the same lists the official web app shows, anonymously and read-only:

```console
$ fiioctl cloud list
  0  FH3 FOR KA15        [fiio-official]   10 bands, preamp +8.0 dB, 27 dl
  1  FF5 FOR KA15        [fiio-official]   10 bands, preamp +6.0 dB, 26 dl
  2  FT1 FOR KA15        [fiio-official]   10 bands, preamp -6.0 dB, 20 dl
  ...
  9  Simgot EW300 to VDSF Target  [fiio-community] by ...  10 bands, 10 dl
```

`cloud show <n>` prints the bands and curve; `cloud apply <n>` writes it. Needs
`pip install 'fiioctl[cloud]'`. Protocol details, including the AES/RSA envelope
and FiiO's measurement service, are in [docs/CATALOGUE.md](docs/CATALOGUE.md).

## Headphone EQ profiles

`eq search` and the terminal UI's library search the same three sources and list
them together, best match first: FiiO's presets for your model, FiiO's measurement
database, and [AutoEq](https://github.com/jaakkopasanen/AutoEq), the measurement
data FiiO's own app credits as its source. Community presets always come last, so
`eq apply` only picks one by default when nothing else matches.

FiiO's presets are per model, so they are searched when a device is connected; to
search them with nothing plugged in, name a model with `--product`. The indexes
are cached locally, and a source that cannot be reached is skipped with a warning.

`eq apply` takes the same search and applies a result from any source. It maps a
profile onto the device's bands, clamps gains to what the hardware accepts,
flattens any bands the profile does not use, and tells you if the profile had more
bands than the device has. A result's `id` from `eq search --json` also works in
place of the search, and keeps naming the same profile as the results change.

## FiiO's measurement database (~4500 headphones)

Bigger than AutoEq's public set, and it includes per-variant measurements. This
runs the same server-side AutoEq the official web app uses, so the result matches
what that UI would produce:

```console
$ fiioctl autoeq refresh
refreshed: 4544 measurements, 4578 targets

$ fiioctl autoeq search "dt177x go"
   714  Beyerdynamic DT 177X Go  [leather earpads]  -  oratory1990
   715  Beyerdynamic DT 177X Go  [velour earpads]  -  oratory1990

$ fiioctl autoeq show 714
Beyerdynamic DT 177X Go (leather earpads)  [fiio-autoeq/oratory1990]
  target: Harman over-ear 2018  (id 6)
  preamp -6.6 dB
      105 Hz    -7.5 dB  Q  0.70  low-shelf
      133 Hz    -5.9 dB  Q  1.15  peak
      ...
    10000 Hz    +2.4 dB  Q  0.70  high-shelf

$ fiioctl autoeq apply 714 --save
```

The target curve is chosen the way FiiO chooses it, matching the measurement's
rig and form factor against their own recommendation table, or pick one yourself
with `--target <id>` after browsing `fiioctl autoeq targets harman`.

The two indexes (~800 KB) are cached locally for 14 days; `autoeq refresh`
re-downloads them. Individual frequency-response curves are fetched lazily and
then cached, rather than mirroring ~90 MB of data nobody asked for.

## Scripting and agents

Every command takes `--json` and returns a stable exit code: `0` ok, `2` usage,
`3` device error, `4` data error.

`fiioctl schema` dumps the entire model (framing, every command with its ID,
codec, range and capability, the per-device registry, and the batch protocol)
as one JSON document. That is the intended way for an agent to discover what it
can do, instead of parsing `--help`.

For many operations in a row, `fiioctl batch` reads one JSON object per line and
keeps the device open, which removes both interpreter startup and the
~10–50 ms device-open cost from each step:

```console
$ printf '%s\n' \
    '{"op":"set","name":"eq-gain","value":-6}' \
    '{"op":"band","index":0,"freq":105,"gain":5.5,"q":0.7,"filter":"low-shelf"}' \
    '{"op":"get","name":"volume"}' | fiioctl batch
{"ok": true, "value": -6.0, "request": "..."}
{"ok": true, "request": "..."}
{"ok": true, "value": 42, "request": "..."}
```

`--dry-run` builds and prints packets without writing, and works with no device
attached. That is useful for testing and for inspecting what a command would send:

```console
$ fiioctl --dry-run peq set 2 --freq 9140 --gain 5.6 --q 3.46
TX 07 aa 0a 00 00 15 08 02 00 38 23 b4 01 5a 00 00 ee
band 2: 9140 Hz +5.6 dB Q 3.46 peak
```

## Extending it

Adding a decoded command is one line in `fiioctl/registry.py`:

```python
"my-setting": Cmd(42, "u8", help="..."),
```

Adding a device is one entry in the same file's `DEVICES` table: report ID,
command pacing, gain range, preset map, renameable slots. No code changes.

Undecoded commands are listed in `registry.UNMAPPED` and reachable right away:

```console
$ fiioctl raw 27                 # send cmd 27 with no payload
$ fiioctl raw 53 --read          # GET cmd 53 and show the reply bytes
$ fiioctl --trace state          # watch every packet
```

## Layout

```
core (no optional dependencies)
  fiioctl/proto.py      framing + value codecs        77
  fiioctl/curve.py      RBJ biquads + text plot      102
  fiioctl/registry.py   command + device data        116
  fiioctl/eqdb.py       AutoEq search, PEQ files     117
  fiioctl/device.py     HID transport, pacing        174
  fiioctl/cli.py        argparse surface             499
optional
  fiioctl/ui.py         terminal UI    [ui]          648
  fiioctl/cloud.py      FiiO catalogue [cloud]       124

docs/PROTOCOL.md      the reverse-engineered HID protocol
docs/CATALOGUE.md     FiiO's online services
packaging/            Linux udev rule
tests/                61 tests, no hardware or network needed
```

Line counts exclude blanks, comments and docstrings.

## Verification status

Be aware of what has and has not been checked:

- **Verified on hardware** (KA15, firmware 1.1.6): device discovery, the reply
  parser, `info`, `state`, `get`, `peq show`, preset-name reads, and the write
  path.
- **Verified against FiiO's live services**: `cloud list` / `cloud show`
  (39 presets for the KA15) and `autoeq refresh` / `search` / `show`
  (4544 measurements, 4578 targets, server-side equalisation). Reading preset 6 returned a coherent octave-spaced hiphop curve and the
  user slots decoded as real names (`FD15`, `FF5`, `FH3`), which together confirm
  the band codec and the slot-offset-of-7 quirk.
- **Verified against the bundle**: every packet `fiioctl` builds was compared
  byte-for-byte with the official app's own encoders, and the codecs round-trip
  under `pytest` (`tests/test_proto.py`). AutoEq search and profile parsing run
  against live data.
- **Write path verified on hardware**: `autoeq apply … --save` wrote all ten
  bands and read back identically, `eq save` persisted to a user slot, and
  `eq name` round-tripped. Round-tripping values through the device is also what
  turned up two defects since fixed: the global gain's whole-decibel
  quantization and the uncleared preset-name field (both in
  [docs/PROTOCOL.md](docs/PROTOCOL.md)).
- **Not yet exercised**: `factory-reset`, and `peq clear` on a device with
  settings worth keeping.
- **Best-effort**: the non-KA15 entries in `DEVICES`, transcribed from the app's
  per-device tables rather than observed. `fiioctl info` reports
  `verified_on_hardware` so you know which you are relying on.

### Devices it cannot drive

FiiO's own app uses three different transports, and this project implements one
of them, the shared HID command set. Two groups are therefore out of reach, and
`fiioctl` says so rather than failing obscurely:

| | Devices |
|---|---|
| different protocol (adapter) | KG13, BTR15, JadeAudio Trustblu, FG3, JK13, SNOWSKY GAMDONG, JD1 Gaming |
| USB CDC serial, not HID | K15 (confirmed); K17/K19 possibly |

```console
$ fiioctl info
error: FIIO BTR15: the official app drives this model through a different
       protocol, which fiioctl does not implement. See docs/PROTOCOL.md.
       Pass --force to talk to it anyway as if it were an HID device.
```

Everything else the app supports uses the same command numbers, so volume, EQ,
presets, filter and balance should work; see
[docs/PROTOCOL.md](docs/PROTOCOL.md) for the evidence.

Use `--dry-run` to inspect packets without writing, and `--trace` to watch real
traffic. `fiioctl devices` and `fiioctl info` report which profile was matched.

Own anything other than a KA15? [CONTRIBUTING.md](CONTRIBUTING.md) explains how to
test it, and what it would take to support the models above.

## TODO

- **Firmware flashing.** The app's `update-firmware` / `kt1213-update` chunks
  implement it. Held back deliberately: it is the only operation that can brick
  the device, so it needs its own verification pass rather than being bundled in
  with settings control.
- Bluetooth controls (codec selection, pairing) for the BTR/BT11 devices. The
  commands live in the app's `bt11-cmd` and `tengtai-controller` chunks.
- The dynamics processor (compressor/limiter) commands, 53–57.

## Credits

Protocol derived from the FiiO Control web app as vendored by
[Ircama/fiiocontrol](https://github.com/Ircama/fiiocontrol) and verified against
hardware. Headphone measurements by
[AutoEq](https://github.com/jaakkopasanen/AutoEq).

MIT licensed. See [LICENSE](LICENSE), and [ATTRIBUTION.md](ATTRIBUTION.md) for
full provenance and the licences of everything consulted. Not affiliated with or
endorsed by FiiO.

[CHANGELOG.md](CHANGELOG.md) covers what changed per release.
[SECURITY.md](SECURITY.md) covers how to report a vulnerability, what this tool
touches, and why the AES-ECB in the catalogue client is FiiO's choice rather
than ours.
