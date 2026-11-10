# FiiO HID protocol

Reverse-engineered from the FiiO Control web app bundle (as vendored by
[Ircama/fiiocontrol](https://github.com/Ircama/fiiocontrol)) and verified against
a FiiO KA15 on firmware 1.1.6.

Every identifier below is the app's own (minified) symbol where it helps, so the
findings can be re-verified against a future bundle.

## Framing

One layout for both directions (app symbol `zt`):

```
[hdr0, hdr1, 0x00, 0x00, cmd, len, *data, 0x00, 0xEE]
```

| Direction | hdr0 | hdr1 |
|---|---|---|
| write a value (SET) | `0xAA` | `0x0A` |
| read a value (GET)  | `0xBB` | `0x0B` |

Device replies carry the GET header. Payload extraction is `reply[6 : 6 + reply[5]]`
(app symbol `aot`) — byte 5 is the payload length. There is no checksum; `0xEE`
terminates. Multi-byte integers are **big-endian** (`$C` encodes, `Ri` decodes,
with optional sign extension).

Frames are sent as HID output reports with a leading report-ID byte.

### Observed on hardware

Confirmed against a **KA15 on firmware 1.1.6**. Two details the bundle does not
make obvious:

1. **Byte 3 of a reply is a rolling sequence counter**, not the zero that
   requests carry. Consecutive reads returned `0x22` then `0x23`:

   ```
   TX  07 bb 0b 00 00 0b 00 00 ee ...          (report id 07, GET cmd 11)
   RX  07 bb 0b 00 22 0b 03 01 01 06 ee ...    (seq 0x22, cmd 11, len 3, "1.1.6")
   RX  07 bb 0b 00 23 0b 03 01 01 06 ee ...    (seq 0x23)
   ```

   Command/length offsets are unaffected, so a parser keyed off byte 4 and 5
   works either way — but do not assume byte 3 is zero.

2. **Reports carry stale bytes after the terminator** (`... ee 80 00 47 00 58 ee`).
   Always trust the length field rather than scanning for `0xEE`.

Replies put the payload immediately before `0xEE`, with no `0x00` pad — unlike
requests, which do include one.

## Report IDs

From the app's `l3e` table with `vv()` as the accessor — only non-default
devices are listed, everything else (including the **KA15**) uses `7`:

| Device | Report ID |
|---|---|
| FIIO KA17 | 1 |
| JadeAudio JA11 | 2 |
| USB20 | 0 |
| *everything else* | **7** |

## Three ways the app drives a device

The official app does not drive every device the same way, which bounds how far
this project reaches.

| Transport | Devices | Here |
|---|---|---|
| **Shared HID command set** | KA15, KA17, JA11, BTR13, BTR17, K13 R2R, K17, K19, QX13, FP3, Melody, JIEZI, RETRO NANO … | implemented |
| **Adapter controller** (`R4e` and `GM` in the bundle) | KG13, BTR15, JadeAudio Trustblu, FG3, JK13, SNOWSKY GAMDONG, JD1 Gaming | same HID commands; untested, so refused |
| **USB CDC serial** (`initPortCtrl`, `connectType: SERIAL`) | K15 confirmed; K17/K19 are special-cased and may be too | not implemented |

The app's adapter factory returns a controller for those seven models and
`undefined` for everything else, and "everything else" is what falls through to
the shared command set documented below. For the K15 the app throws *"Port
controller is undefined"* without a serial connection, so it is not an HID device
at all.

Despite the name, the adapter is a code path in the app and not a different wire
protocol. Its controller (`GM`) builds frames with the same function, headers,
command ids and value encodings as the shared set below, and report id 7 applies
as it does to most models. Five models use it unchanged. Two wrap it: the KG13
(`I4e`) listens for the device announcing a preset change on cmd 22 and skips some
preset writes, and the BTR15 (`L4e`) waits a second after a preset write, treats
the EQ switch as always on, and resets its bands one at a time.

What an implementation would have to handle, from the bundle's `index-WZB3nC8k.js`:

- **Preset values can be longer than one byte.** The FG3 and JD1 Gaming use 13,
  160-162, 257-260 and 513. Cmd 22 carries them big-endian at whatever length they
  need, and the reply states its length. `fiioctl` treats `eq-preset` as one byte.
- **Per-model tables differ.** The FG3 has six filter types with no all-pass, and
  mic, output and mic-monitor controls.
- **Commands 53 and 54** are read and written as 3D gain and reverb level.

This comes from reading the bundle only. No adapter model has been tried, so none
can be called supported yet.

`registry.Device.transport` records all this. `fiioctl` refuses a serial model,
which it cannot speak to, and an adapter model, which nobody has tested. `--force`
overrides that for experimentation.

One caveat on the HID row: **only the KA15 has been confirmed on hardware**
(`Device.verified`). The others are listed because the app's own tables give
their report id and parameter ranges, not because anyone has tried them.

## Device selection

USB filters from the app (`T5e`), as decimal/hex:

| Vendor | VID | Notes |
|---|---|---|
| FiiO | 10610 = `0x2972` | any PID |
| — | 13058 = `0x3302` | PIDs 15296–15298 |
| — | 2578 = `0x0A12` | PID 16391 |
| — | 1638 = `0x0666` | PID 2184 |
| — | 12722 = `0x31B2` | PID 65528 |

The app prefers the interface whose HID usage page is vendor-defined
(`>= 0xFF00`) and whose collection carries both an input and an output report
with the device's report ID.

In practice this is only a preference. A **KA15 exposes exactly one HID
interface, on usage page `0x000C` (Consumer Control)**, and control traffic works
fine over it:

```
FIIO KA15   2972:0104   usage=0x000c   iface=0
```

So vendor-page selection must fall back to "the only interface there is" rather
than requiring `>= 0xFF00`.

## Command pacing

The app funnels every command through a serial queue with `concurrency: 1` and a
per-device minimum interval (`w3e`), and a 2 s timeout (`QE`):

| Interval | Devices |
|---|---|
| 100 ms | **KA15**, JA11, BTR13/15/17, QX13, K15/K17/K19, Melody, Tiny A/B, … |
| 200 ms | KA17, K13, K13 R2R, BR15 R2R, AIR AMP |
| 300 ms | AIR LINK, BT11 |

Ignoring this is the most likely cause of dropped writes, so `fiioctl` paces
writes the same way.

## Commands

`mode` is read/write support; `r` means GET-only.

| cmd | name | mode | payload | app symbols |
|---:|---|---|---|---|
| 1 | volume-max | rw | u8 | `kX` / `MX` |
| 2 | volume | rw | u8 | `BX` / `NX` |
| 4 | filter | rw | u8 (1–7) | `zX` / `UX` |
| 6 | *unknown* | rw | u8 | `oot` / `iot` |
| 7 | balance | rw | `[left, right]`, see below | `VX` / `FX` |
| 8 | volume-switch* | rw | bool | `GX` / `$X` |
| 11 | firmware | r | version bytes | `PX` |
| 16 | screen* | rw | u8 | `IX` / `LX` |
| 18 | mic-switch | rw | bool | `qX` / `HX` |
| 21 | **PEQ band** | rw | see below | `SX` / `wX` |
| 22 | eq-preset | rw | u8 | `HM` / `xX` |
| 23 | eq-gain | rw | s16, dB × 10 | `CX` / `TX` |
| 24 | eq-count | r | u8 | `DX` |
| 25 | eq-save | w | u8 slot | `RX` |
| 26 | eq-switch | rw | bool | `qM` / `_w` |
| 27 | *unknown action* | w | none | `OX` |
| 29 | mic-monitor | rw | bool | `ZX` / `XX` |
| 30 | mic-monitor-volume | rw | u8 | `jX` / `YX` |
| 33 | factory-reset | w | u8 | `QX` |
| 34 | mic-volume | rw | u8 | `KX` / `WX` |
| 48 | preset name | rw | `[slot, *utf8]` | `EX` / `AX` |
| 51 | led | rw | u8 | `JX` / `eZ` |
| 52 | mic-nr | rw | u8 | `rZ` / `tZ` |
| 53–57 | *unknown* | rw | — | `q4e`…`Q4e` |

Unknowns are listed in `registry.UNMAPPED` and reachable with `fiioctl raw`.

\* These two ids do not mean the same thing on every model: on a KA15, 8 is
the headset mic and 16 is screen rotation. Each `Device` says which name
applies to it, so the bundle's names are never renamed globally.

## The KA15's settings menu

Everything below was mapped on a KA15 on firmware 1.1.6, and none of it comes
from the app bundle: the app never sends commands 5, 12, 13, 15, 17 or 50, so
they are firmware extras rather than part of the shared command set. Whether
the KA15's screen-equipped siblings use the same ids is untested.

Method: a read-only loop polled every command that answers, once a second, with
a timestamp on each change, while every item on the device's menu was changed
in a stated order. Matching the order of logged changes to the order of the
walk gives the mapping, and two items corroborate it independently - `disp-off`
showed seven distinct values when its list was swept, and `vol` moved commands
1 and 2 along with itself.

| cmd | menu item | payload | host-writable | notes |
|---:|---|---|---|---|
| 5 | dac | u8 | yes | 0 class AB, 1 class H; accepts 0-7, the rest unidentified |
| 8 | mic | u8 | yes | **0 is enabled**, 1 disabled |
| 12 | vol | u8 | yes | changes what cmds 1 and 2 mean; see below |
| 13 | key mode | u8 | **no** | the device sets it; every host write but the current value is refused |
| 14 | brightness | u8 | yes | 0-10 |
| 15 | disp-off | u8 | yes | 7 values, 2 is "1 min"; the rest are unlabelled |
| 16 | rotation | u8 | yes | 0 upright |
| 17 | language | u8 | yes | **only 0 and 1 accepted**: Chinese and English |
| 32 | uac mode | u8 | yes | 0 is UAC 1.0, 1 is 2.0; **writing it re-enumerates the device** |
| 50 | dre | bool | yes | 1 enabled |

`vol` (cmd 12) is not an independent setting: switching it rescaled volume-max
(cmd 1) from 120 to 60 and volume (cmd 2) from 64 to 128 in one step. So the
volume commands cannot be read in isolation, and a client that caches a volume
range needs to re-read it after this changes.

Writing cmd 32 takes the device off the USB bus and brings it back. `hid_write`
can then report -1 for a write that landed, so `Dac.set` treats the ids in
`registry.RESETS_LINK` as applied-but-unverifiable rather than failed.

### Not exposed over HID

Negative results, each from a full sweep of commands 1-127 rather than an
assumption:

- **theme**, swept through every value on the device, moved nothing.
- **s/pdif** and the **voltage and current display**, toggled on the device,
  moved nothing. All three are device-local.
- The **headphone jack** is not reported. Moving the headphone between the
  3.5 mm and 4.4 mm sockets changed no command in the whole space.
- Nothing answers above command 50 on this model.

Two commands answer but remain unidentified, and no menu item accounts for
either: **cmd 3** takes writes across 0-4, and **cmd 9** is read-only, refusing
every value but the 2 it already holds.

### PEQ band (cmd 21)

8-byte payload, from the app's `SX(index, freq, gain, q, type)`:

```
[index, gain*10 (s16 BE), freq Hz (u16 BE), q*100 (u16 BE), filterType]
```

Negative gain is 16-bit two's complement. Filter types (`Xe`):

| 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| PEAK | LOW_SHELF | HIGH_SHELF | BAND_PASS | LOW_PASS | HIGH_PASS | ALL_PASS |

`GET 21` takes `[index]` and replies with the same layout.

### Channel balance (cmd 7)

The app sends `value > 0 ? [0, value] : value < 0 ? [value, 0] : [0, 0]`, where a
negative value becomes a two's-complement byte via `Uint8Array` coercion.
`fiioctl` reproduces this byte-for-byte rather than guessing a cleaner encoding.

### Value resolution (measured, KA15 fw 1.1.6)

The wire format carries more precision than the hardware keeps. Round-tripping
every field through a real device gives:

| Field | Wire | Actually stored |
|---|---|---|
| band gain (cmd 21) | dB x 10 | **exact** — -7.5, -7.4, +6.7 all survive |
| band frequency | Hz | exact |
| band Q | Q x 100 | **up to ~0.02 drift, and not stable** — 0.71→0.70, 2.79→2.80, 5.76→5.78, then 5.78→5.80 if written back |
| **EQ global gain (cmd 23)** | dB x 10 | **whole decibels only**, truncated toward zero |

Band edits that have not been saved also stay with their preset when the active
preset changes, so switching away and back does not restore the saved EQ.

The global gain is the one that matters. Measured:

```
sent -6.6 -> -6.0     sent -6.5 -> -6.0     sent -6.4 -> -6.0
sent -3.3 -> -3.0     sent +2.7 -> +2.0     sent ±0.5 ->  0.0
```

Two consequences worth designing around:

1. A UI step finer than 1 dB makes alternate keypresses look like they did
   nothing. `registry.Device.gain_step` records the real resolution.
2. An AutoEq pre-amp of -6.6 dB lands as -6.0 — **0.6 dB less attenuation than
   the profile asked for, which is a clipping risk.** Pre-amps must therefore be
   rounded *downward* before sending, never truncated. `Dac.snap_gain(v,
   safe=True)` does this.

Every FiiO preset in their own catalogue carries a whole-number `masterGain`,
which suggests this is general rather than KA15-specific, but only the KA15 has
been measured.

### Preset names (cmd 48)

UTF-8, max 8 characters for most devices (`mR`), non-alphanumerics stripped
(`replace(/\W/g, "")`). The **KA15 subtracts 7 from the slot number** before
naming, so USER1 (preset 7) is name index 0 — see `setPreNameByCtrl`.

**The name field is a fixed 8 bytes and the firmware does not clear it.** Writing
a short name leaves whatever was there before, and a read returns the whole
field. A real example, reading a slot named `M6`:

```
RX 07 bb 0b 03 33 30 08 | 01 4d 36 27 10 00 46 02 | 00 33 ee
                 cmd len   slot  M  6  '  \x10 \0  F  \x02
```

`27 10` is 10000 and `00 46` is 70 — the frequency and Q of an **earlier PEQ band
write** still sitting in the buffer. Naively decoding that yields `M6' F`.

So: pad the name to the full field width with NULs when writing, and when
reading, stop at the first byte outside printable ASCII rather than merely
stripping trailing NULs. The official app only filters to printable characters
(`lot = e => e.filter(t => t >= 32 && t <= 126)`), which still lets a stray
`'` through — so it presumably shows the same artefact.

## KA15 specifics

| Property | Value | Source |
|---|---|---|
| USB ID | `2972:0104` | observed |
| HID usage page | `0x000C`, single interface | observed |
| Report ID | 7 (the default; KA15 is not in `l3e`) | bundle |
| Min command interval | 100 ms | bundle |
| EQ global gain range | −12 … +12 dB | bundle |
| Presets | jazz 0, pop 1, rock 2, dance 3, rb 4, classic 5, hiphop 6, USER1 7, USER2 8, USER3 9, **off 10** | bundle |
| Renameable slots | 7, 8, 9 (name index = slot − 7), 8 chars | bundle + observed |
| Band count | 10, read from cmd 24 | observed |
| Firmware tested | 1.1.6 | observed |

Note the KA15 uses `10` for "EQ off" and has no `240`/BYPASS entry, unlike some
siblings.

Two behaviours found only by testing:

- **No independent EQ switch.** Command 26 (`eq-switch`) never replies on the
  KA15; bypass is done with preset `10`. `registry.Device.unsupported` records
  this so `fiioctl state` does not stall on it.
- **Filter mode reports `0`** from the factory. The valid set is evidently
  device-specific, so `fiioctl` treats cmd 4 as an advisory 0–7 range rather than
  a fixed enum.

A factory read of preset 6 (hiphop) returned a textbook octave-spaced curve,
which is what validated the band codec end to end:

```
global gain  -7.0 dB
   0      32 Hz    +3.0 dB  Q 0.71  peak
   4     500 Hz    -2.5 dB  Q 0.71  peak
   9   16000 Hz    +3.0 dB  Q 0.71  peak
```

## The online EQ catalogue

FiiO Control's "handpicked" / "official recommendation" headphone lists are **not
a plain REST API**. They are served from relative paths (`/ucenter-api`,
`/usersystem-api`, `/fasthub`) on the app's own origin, and every request body is
encrypted: an AES key per request, RSA-wrapped, sent as `cipherSign` +
`cipherText` with an `X-Request-ID` header.

`fiioctl` therefore sources headphone profiles from **AutoEq**, which FiiO's own
about page credits as the origin of its measurement data. That keeps the feature
offline-cacheable and free of a brittle crypto handshake. Re-implementing the
FiiO catalogue is possible but would need the RSA public key and the response
decryption path lifted out of the bundle.

## Not implemented: firmware flashing

The app's `update-firmware` / `kt1213-update` chunks drive firmware writes. This
is deliberately out of scope for v1 — it is the one operation that cannot be
undone from software if a packet goes astray. See the README TODO.
