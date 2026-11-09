# Contributing

Only the **FiiO KA15** has been confirmed against real hardware, and it is the
only device I have. A report from anything else is the most useful contribution
there is, whether it works or not.

## Test your device

These models speak the protocol fiioctl implements, but their entries come from
FiiO's own tables rather than from anyone trying them:

JadeAudio JA11, KA17, BTR13, BTR17, K13 R2R, K17, K19, FP3, QX13, SNOWSKY Melody,
JadeAudio JIEZI, RETRO NANO.

If you have a different FiiO device, the next section may be the one you want.

### 1. Read-only

Install it as the [README](README.md#install) describes, then run these and
paste the output into a
[device report](https://github.com/ethanbarclay/fiioctl/issues/new?template=device_report.yml):

```console
$ fiioctl devices
$ fiioctl --json info
$ fiioctl --json state
$ fiioctl scan
$ fiioctl peq show
```

None of these change anything on the device. In 0.1.0, `--json` has to come
before the subcommand, whatever the report form says.

### 2. Reversible writes, if you are up for it

Take your headphones off first. A wrong volume or gain on a model nobody has
tested can be very loud.

Note each value before changing it, change it, check it on the device or in
FiiO's app, then put it back:

```console
$ fiioctl get volume
$ fiioctl set volume <one step away>

$ fiioctl eq preset
$ fiioctl eq preset <another preset>

$ fiioctl peq export before.txt
$ fiioctl peq set 0 --freq 1000 --gain 1 --q 1
$ fiioctl peq show
$ fiioctl peq import before.txt
```

On the KA15, switching presets does not discard band edits, so use the import to
put them back. It can leave a few Q values about 0.02 off, because the firmware
adjusts Q slightly when it is written.

Say in the report what happened, and include the firmware version from
`fiioctl --json info`.

### What not to run while testing

- **`set factory-reset`**, for the obvious reason. It has never been exercised,
  even on the KA15.
- **`eq save`**, or `--save` on anything. It overwrites a preset slot, and fiioctl
  only knows which slots are factory ones on the KA15.
- **`raw`** with a command you do not know. Some of the undecoded ones are actions
  rather than settings.
- **`--force`**. See below.

### Turning a report into a pull request

Most models need one edit to their entry in `DEVICES` in
[`fiioctl/registry.py`](fiioctl/registry.py): the quirks you found, such as
`gain_step`, `bands`, `caps` or `unsupported`, and `verified=True` once it has
been confirmed. No other code changes should be needed.

To work out what an undecoded setting does, take a snapshot, change exactly one
setting in the device's menu or FiiO's app, and diff:

```console
$ fiioctl scan --save before.json
$ fiioctl scan --diff before.json
```

## The other two protocols

FiiO's app drives some models in ways fiioctl does not implement, so these need
real engineering rather than a registry entry:

| | Devices |
|---|---|
| Adapter protocol | KG13, BTR15, JadeAudio Trustblu, FG3, JK13, SNOWSKY GAMDONG, JD1 Gaming |
| USB CDC serial | K15 confirmed; K17 and K19 possibly |

I cannot test any of these, so support depends on someone who owns one. If that
is you, open an issue (or add to an existing one for your model) saying which
model it is and how far you are willing to go. These help, roughly in order:

1. The output of `fiioctl --json devices`, which does not open the device, and
   your OS.
2. USB captures of FiiO's official app while you change one setting at a time,
   using Wireshark with usbmon on Linux or USBPcap on Windows. This is passive, so
   it is safe for the device. Captures can include its serial number.
3. Running test builds and sending back the `--trace` output.

**Please do not use `--force` on these models.** It sends them frames from a
protocol they are not built to understand, and nobody knows how each one reacts.

For anyone writing the code:

- The protocol lives in the FiiO Control web app bundle. See
  [docs/PROTOCOL.md](docs/PROTOCOL.md) for where to find it and how the shared
  protocol was derived. Whether the adapter models share one protocol is not
  known yet.
- A new transport belongs behind `Dac` in [`fiioctl/device.py`](fiioctl/device.py),
  so the CLI and TUI do not need to know about it.
- Build it up so that a mistake cannot do much: identify the device and read
  values first, then one reversible write such as volume, and EQ last.
- Firmware flashing is held back on purpose, because it is the one operation that
  can brick a device. It needs its own verification pass.

## Working on the code

```console
$ git clone https://github.com/ethanbarclay/fiioctl
$ cd fiioctl
$ python -m venv .venv && source .venv/bin/activate
$ pip install -e ".[dev]" ruff
$ python -m pytest -q
$ ruff check .
```

It needs Python 3.10 or later. The tests need neither a device nor the network,
and should stay that way: the HID layer and FiiO's services are stubbed out, so CI
can run them anywhere. `--dry-run` shows the packets a command would send without
a device attached, and `--trace` shows real traffic.

Adding a decoded command or a device is covered in
[Extending it](README.md#extending-it).

When a change adds to what is known about the protocol, record it in
`docs/PROTOCOL.md` and say where the finding came from: the app's bundle, or a
device, and which one.

## Commits and pull requests

Commit subjects are lowercase, start with a [gitmoji](https://gitmoji.dev), and
say what the change does:

```
:bug: refuse an on/off value that is neither
:sparkles: add --version, with one source for the number
:memo: add a changelog for 0.1.0
```

The usual ones here are `:sparkles:`, `:bug:`, `:recycle:`, `:memo:`,
`:white_check_mark:`, `:wrench:`, `:construction_worker:` and `:lock:`.

Pull requests use the template. The section that matters most is **Testing**: say
what was actually run, which parts were verified on hardware, and on which device
and firmware. "Only tested against the fakes" is a fine answer, as long as it is
the true one.

Report anything that looks exploitable privately, as [SECURITY.md](SECURITY.md)
describes, rather than in a public issue.

Contributions are accepted under the project's [MIT license](LICENSE).
