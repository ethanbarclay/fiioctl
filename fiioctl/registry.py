"""Declarative device and command registry.

Everything the CLI can do is a row in :data:`COMMANDS` or a field in
:data:`DEVICES`. Adding support for another FiiO device, or another command
decoded from the official app, means editing data here — not code.

Command IDs, codecs and per-device quirks were extracted from the FiiO Control
web app bundle (see docs/PROTOCOL.md).
"""

from dataclasses import dataclass, field

#: USB vendor ID -> the product IDs the official app's WebHID filters accept
#: (`T5e` in the bundle). None means any product from that vendor.
VENDORS = {
    0x2972: None,
    0x3302: (15296, 15297, 15298),
    0x0A12: (16391,),
    0x0666: (2184,),
    0x31B2: (65528,),
}


@dataclass(frozen=True)
class Cmd:
    """One protocol command."""

    id: int
    codec: str = "u8"
    mode: str = "rw"  # 'r', 'w', or 'rw'
    help: str = ""
    rng: tuple | None = None  # (min, max) advisory range for the value
    enum: dict | None = None  # name -> value, for symbolic arguments

    @property
    def readable(self):
        return "r" in self.mode

    @property
    def writable(self):
        return "w" in self.mode


# Which filter indices are valid is device-specific: the KA15 reports 0 from the
# factory, other models start at 1. Kept as an advisory range, not a fixed enum.
FILTER_RANGE = (0, 7)

#: CLI name -> command. Names are the user-facing vocabulary.
COMMANDS = {
    "volume": Cmd(2, "u8", help="Current output volume step"),
    "volume-max": Cmd(1, "u8", help="Maximum volume limit"),
    "volume-switch": Cmd(8, "bool", help="Fixed/variable line-out switch"),
    "balance": Cmd(7, "balance", rng=(-20, 20), help="Channel balance; >0 attenuates right"),
    "filter": Cmd(4, "u8", rng=FILTER_RANGE, help="DAC digital filter / roll-off mode"),
    "eq-preset": Cmd(22, "u8", help="Active EQ preset slot"),
    "eq-switch": Cmd(26, "bool", help="Master EQ on/off"),
    "eq-gain": Cmd(23, "x10", rng=(-12, 12), help="EQ global pre-amp gain in dB"),
    "eq-count": Cmd(24, "u8", mode="r", help="Number of PEQ bands the device exposes"),
    "firmware": Cmd(11, "version", mode="r", help="Firmware version"),
    "screen": Cmd(16, "u8", help="Screen orientation"),
    # Identified on a KA15 by changing each setting on the device while a
    # timestamped read loop watched every command that answers, then matching
    # the order of the changes to the order of the menu walk.
    #
    # Two groups, and the difference matters. 5, 12, 13, 15, 17 and 50 appear
    # nowhere in the app bundle, so they are firmware extras rather than part
    # of the shared command set. 8 and 16 *are* in the bundle, under other
    # names: on a KA15 cmd 8 is the headset mic rather than a line-out switch.
    # So `mic` and `rotation` are separate entries on the same ids, and each
    # model says through `caps` and `unsupported` which name applies to it.
    # Renaming them outright would mislabel every other model.
    "language": Cmd(17, "u8", enum={"chinese": 0, "english": 1},
                    help="On-screen menu language"),
    "brightness": Cmd(14, "u8", rng=(0, 10), help="Screen brightness, 0-10"),
    "rotation": Cmd(16, "u8", help="Screen rotation; 0 is upright"),
    "disp-off": Cmd(15, "u8", rng=(0, 6),
                    help="Screen blanking timeout; 2 is '1 min' on a KA15"),
    "dac-mode": Cmd(5, "u8", enum={"class-ab": 0, "class-h": 1},
                    help="DAC output stage mode"),
    "vol-mode": Cmd(12, "u8",
                    help="Volume scale; changes what volume and volume-max mean"),
    "key-mode": Cmd(13, "u8", mode="r",
                    help="Physical key mapping; the device sets it, writes are refused"),
    "uac-mode": Cmd(32, "u8", enum={"uac1": 0, "uac2": 1},
                    help="USB Audio Class; writing it re-enumerates the device"),
    "dre": Cmd(50, "bool", help="Dynamic range enhancement"),
    # 0 is enabled here, so this is an enum rather than a bool: `mic off`
    # reads better than a boolean whose true means disabled.
    "mic": Cmd(8, "u8", enum={"on": 0, "off": 1}, help="Headset microphone"),
    "led": Cmd(51, "u8", help="Indicator LED configuration"),
    "mic-switch": Cmd(18, "bool", help="Microphone enable"),
    "mic-volume": Cmd(34, "u8", help="Microphone input gain"),
    "mic-monitor": Cmd(29, "bool", help="Microphone sidetone/monitor enable"),
    "mic-monitor-volume": Cmd(30, "u8", help="Microphone sidetone volume"),
    "mic-nr": Cmd(52, "u8", help="Microphone noise-reduction level"),
    # Write-only actions.
    "eq-save": Cmd(25, "u8", mode="w", help="Persist current EQ into a device slot"),
    "factory-reset": Cmd(33, "u8", mode="w", help="Restore factory settings"),
}

#: command name -> capability it needs. Commands absent from this map are
#: assumed present on every device, so `fiioctl state` always probes them.
CAPS = {
    "volume": "volume", "volume-max": "volume", "volume-switch": "volume",
    "balance": "balance", "filter": "filter", "led": "led",
    "screen": "screen", "language": "screen", "brightness": "screen",
    "mic-switch": "mic", "mic-volume": "mic", "mic-monitor": "mic",
    "mic-monitor-volume": "mic", "mic-nr": "mic",
    "rotation": "screen", "disp-off": "screen",
    "mic": "headset-mic", "dac-mode": "dac-mode", "vol-mode": "vol-mode",
    "key-mode": "keys", "uac-mode": "uac", "dre": "dre",
}


def supported(spec, name):
    """Is this name one this model uses?

    Capability data is thin for every model but the KA15 - most profiles
    declare only what the app bundle revealed - so this answers "known to
    apply", not "known to be absent". Callers must not read a False here as
    permission to refuse on its own.
    """
    if name in spec.unsupported:
        return False
    need = CAPS.get(name)
    return need is None or need in spec.caps


def alternative(spec, name):
    """Another name for the same command id that *is* used by this model.

    Two models can use one command id for different things, so the name a user
    reached for may be the wrong one for the device in front of them rather
    than wrong in general. Derived from the ids themselves so that adding a
    pair like `screen`/`rotation` needs no second table to keep in step.
    """
    cmd = COMMANDS.get(name)
    if cmd is None:
        return None
    for other, candidate in COMMANDS.items():
        if other != name and candidate.id == cmd.id and supported(spec, other):
            return other
    return None


#: Writing these ids makes the device drop off the USB bus and come back, so
#: the write can report a failure it did not have and nothing can be read back
#: until it re-enumerates. Confirmed for cmd 32 on a KA15.
RESETS_LINK = {32}

#: Commands whose payload is a structured PEQ band rather than a scalar.
BAND_CMD = 21
#: Preset-name command: payload is [slot, *utf8].
NAME_CMD = 48

#: Command ids that perform an irreversible action. `fiioctl scan` never probes
#: these, in case a firmware acts on the command byte without checking whether
#: the frame was a read or a write.
DESTRUCTIVE = {25: "save EQ to slot", 27: "unknown action", 33: "factory reset"}

#: Commands observed in the app bundle but with no confirmed semantics. Reachable
#: through `fiioctl raw`; promote them here as they get identified.
UNMAPPED = {3: "unknown setting (writable; a KA15 accepts 0-4)",
            6: "unknown (get/set, 1 byte)",
            9: "unknown status (read-only; a KA15 accepts only its current value)",
            19: "echo of the preset-name latch, 10 bytes wide; ignores its payload",
            27: "unknown action (no payload)",
            53: "dynamics?", 54: "dynamics?", 55: "dynamics?", 56: "dynamics?", 57: "dynamics?"}

GENERAL_PRESETS = {
    "jazz": 0, "pop": 1, "rock": 2, "dance": 3, "rb": 4, "classic": 5, "hiphop": 6,
    "user1": 7, "user2": 8, "user3": 9,
}


@dataclass(frozen=True)
class Device:
    """Per-model facts that the protocol itself does not carry."""

    name: str
    report_id: int = 7
    interval: float = 0.1  # minimum seconds between commands (app queue pacing)
    timeout: float = 2.0
    gain_range: tuple = (-12.0, 12.0)
    #: resolution of the EQ *global* gain (cmd 23). Per-band gains are always
    #: 0.1 dB, but some devices accept only whole decibels here and silently
    #: truncate the rest toward zero - see docs/PROTOCOL.md.
    gain_step: float = 0.1
    q_range: tuple = (0.1, 20.0)
    bands: int | None = None  # None -> ask the device (eq-count)
    presets: dict = field(default_factory=lambda: dict(GENERAL_PRESETS))
    eq_off: int | None = None  # preset value that means "EQ bypassed"
    name_slots: tuple = ()  # preset slots whose name can be edited
    name_offset: int = 0  # subtracted from the slot before naming
    name_max: int = 8
    caps: tuple = ("eq", "volume")
    #: commands this model does not answer; `state` skips them unless --all
    unsupported: tuple = ()
    #: FiiO's own product id, used by their online preset catalogue (bundle `pX`)
    cloud_id: int | None = None
    #: how this model is actually spoken to. Only "hid" is implemented here; the
    #: others are recorded so the tool can say so instead of failing obscurely.
    #:   "hid"     - the shared HID command set this project implements
    #:   "adapter" - the same HID commands via the app's own controller, untested
    #:   "serial"  - USB CDC serial rather than HID
    transport: str = "hid"
    #: has any of this entry been confirmed against real hardware?
    verified: bool = False


KA15 = Device(
    name="FIIO KA15",
    report_id=7,
    interval=0.1,
    gain_range=(-12.0, 12.0),
    gain_step=1.0,  # measured on firmware 1.1.6: fractions are truncated
    presets={**GENERAL_PRESETS, "off": 10},
    eq_off=10,
    name_slots=(7, 8, 9),
    name_offset=7,  # USER1 (slot 7) is name index 0 on the KA15
    name_max=8,
    # The KA15 has a screen and its own settings menu; every item on it was
    # mapped against firmware 1.1.6 except theme, s/pdif and the voltage and
    # current display, which move nothing in the command space and so are
    # device-local. The headphone jack is not reported either.
    caps=("eq", "peq", "preset-names", "volume", "filter", "balance", "screen",
          "headset-mic", "dac-mode", "vol-mode", "keys", "uac", "dre"),
    # Verified on firmware 1.1.6: cmd 26 never replies. The KA15 has no separate
    # EQ switch; preset 10 ("off") is how the EQ is bypassed.
    #
    # volume-switch and screen are the app bundle's names for cmds 8 and 16.
    # On this model those ids are the headset mic and screen rotation, so the
    # generic names are refused here and `mic` and `rotation` take their place.
    unsupported=("eq-switch", "volume-switch", "screen"),
    cloud_id=110,
    verified=True,
)

#: Devices the official app drives through its adapter factory (`R4e` in the
#: bundle). Its frames match the shared HID command set, but no model has been
#: tried yet, so these are refused unless forced.
#: match key -> (display name, FiiO catalogue id). The catalogue is an HTTP API
#: keyed by product id, so it works for these models even while they are refused
#: - keep the ids rather than dropping them.
ADAPTER_DEVICES = {
    "FIIO KG13": ("FIIO KG13", 409),
    "FIIO BTR15": ("FIIO BTR15", 24),
    "JADEAUDIO TRUSTBLU": ("JadeAudio Trustblu", 51),
    "FIIO FG3": ("FIIO FG3", 120),
    "FIIO JK13": ("FIIO JK13", 411),
    "SNOWSKY GAMDONG": ("SNOWSKY GAMDONG", 412),
    "FIIO JD1 GAMING": ("FIIO JD1 GAMING", 407),
}

#: Normalised product string -> device spec. The product string reported over USB
#: is matched loosely (see :func:`match`), as the official app does.
DEVICES = {
    "FIIO KA15": KA15,
    "JADEAUDIO JA11": Device(name="JadeAudio JA11", report_id=2, gain_range=(-12.0, 12.0),
                             bands=5, presets={**GENERAL_PRESETS, "off": 10}, eq_off=10,
                             caps=("eq", "peq", "volume", "filter"), cloud_id=109),
    "FIIO KA17": Device(name="FIIO KA17", report_id=1, interval=0.2,
                        gain_range=(-12.0, 12.0),
                        caps=("eq", "peq", "volume", "filter", "balance"), cloud_id=108),
    "FIIO BTR13": Device(name="FIIO BTR13", gain_range=(-12.0, 12.0), cloud_id=31),
    "FIIO BTR17": Device(name="FIIO BTR17", gain_range=(-12.0, 12.0), cloud_id=35),
    "FIIO K13 R2R": Device(name="FIIO K13 R2R", interval=0.2, gain_range=(-24.0, 12.0),
                           cloud_id=43),
    # K17/K19 answer the shared command set, but the app hardcodes their band
    # count instead of reading it and disables their EQ switch, so treat the
    # rest of these numbers as unconfirmed.
    "FIIO K17": Device(name="FIIO K17", bands=31, q_range=(0.4, 128.0),
                       gain_range=(-24.0, 12.0), cloud_id=302),
    "FIIO K19": Device(name="FIIO K19", bands=31, q_range=(0.4, 128.0),
                       gain_range=(-24.0, 12.0), cloud_id=29),
    # The app refuses to drive a K15 without a serial port controller
    # ("Port controller is undefined"), so it is not an HID device at all.
    "FIIO K15": Device(name="FIIO K15", gain_range=(-24.0, 12.0), cloud_id=303,
                       transport="serial"),
    "FIIO FP3": Device(name="FIIO FP3", gain_range=(-12.0, 12.0), cloud_id=111),
    "FIIO QX13": Device(name="FIIO QX13", gain_range=(-12.0, 12.0), cloud_id=113),
    "SNOWSKY MELODY": Device(name="SNOWSKY Melody", gain_range=(-12.0, 12.0), cloud_id=114),
    "JADEAUDIO JIEZI": Device(name="JadeAudio JIEZI", gain_range=(-12.0, 12.0), cloud_id=118),
    "RETRO NANO": Device(name="RETRO NANO", gain_range=(-12.0, 12.0), cloud_id=38),
    # Adapter devices: listed so the tool can explain itself, not to imply support.
    **{key: Device(name=label, transport="adapter", cloud_id=cloud)
       for key, (label, cloud) in ADAPTER_DEVICES.items()},
}

#: Used when a connected device is not in the table: the app's own fallbacks.
DEFAULT = Device(name="unknown", report_id=7, gain_range=(-24.0, 12.0))


def models_using(transport):
    """Names of the models declared as using a given transport."""
    return sorted({d.name for d in DEVICES.values() if d.transport == transport})


def label(name, value):
    """Human-readable name for an enum command's value, or None.

    Raw numbers are useless in output: `language = 1` means nothing, while
    `language = english` does.
    """
    cmd = COMMANDS.get(name)
    if not cmd or not cmd.enum:
        return None
    for text, number in cmd.enum.items():
        if number == value:
            return text
    return None


def match(product_string):
    """Resolve a USB product string to a :class:`Device`, loosely, like the app."""
    if not product_string:
        return DEFAULT
    probe = product_string.strip().upper()
    if probe in DEVICES:
        return DEVICES[probe]
    for key, spec in DEVICES.items():
        if key in probe or probe in key:
            return spec
    return DEFAULT
