"""Declarative device and command registry.

Everything the CLI can do is a row in :data:`COMMANDS` or a field in
:data:`DEVICES`. Adding support for another FiiO device, or another command
decoded from the official app, means editing data here — not code.

Command IDs, codecs and per-device quirks were extracted from the FiiO Control
web app bundle (see docs/PROTOCOL.md).
"""

from dataclasses import dataclass, field

#: USB vendor IDs the official app's WebHID filters accept (`T5e` in the bundle).
VENDORS = (0x2972, 0x3302, 0x0A12, 0x0666, 0x31B2)


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
    "balance": "balance", "filter": "filter", "screen": "screen", "led": "led",
    "mic-switch": "mic", "mic-volume": "mic", "mic-monitor": "mic",
    "mic-monitor-volume": "mic", "mic-nr": "mic",
}

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
UNMAPPED = {6: "unknown (get/set, 1 byte)", 27: "unknown action (no payload)",
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
    caps=("eq", "peq", "preset-names", "volume", "filter", "balance"),
    # Verified on firmware 1.1.6: cmd 26 never replies. The KA15 has no separate
    # EQ switch; preset 10 ("off") is how the EQ is bypassed.
    unsupported=("eq-switch",),
    cloud_id=110,
)

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
    "FIIO BTR15": Device(name="FIIO BTR15", gain_range=(-12.0, 12.0), cloud_id=24),
    "FIIO BTR17": Device(name="FIIO BTR17", gain_range=(-12.0, 12.0), cloud_id=35),
    "FIIO K13 R2R": Device(name="FIIO K13 R2R", interval=0.2, gain_range=(-24.0, 12.0),
                           cloud_id=43),
    "FIIO K17": Device(name="FIIO K17", bands=31, q_range=(0.4, 128.0),
                       gain_range=(-24.0, 12.0), cloud_id=302),
    "FIIO K19": Device(name="FIIO K19", bands=31, q_range=(0.4, 128.0),
                       gain_range=(-24.0, 12.0), cloud_id=29),
    "FIIO K15": Device(name="FIIO K15", gain_range=(-24.0, 12.0), cloud_id=303),
    "FIIO FP3": Device(name="FIIO FP3", gain_range=(-12.0, 12.0), cloud_id=111),
    "FIIO QX13": Device(name="FIIO QX13", gain_range=(-12.0, 12.0), cloud_id=113),
    "SNOWSKY MELODY": Device(name="SNOWSKY Melody", gain_range=(-12.0, 12.0), cloud_id=114),
    "JADEAUDIO JIEZI": Device(name="JadeAudio JIEZI", gain_range=(-12.0, 12.0), cloud_id=118),
    "RETRO NANO": Device(name="RETRO NANO", gain_range=(-12.0, 12.0), cloud_id=38),
}

#: Used when a connected device is not in the table: the app's own fallbacks.
DEFAULT = Device(name="unknown", report_id=7, gain_range=(-24.0, 12.0))


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
