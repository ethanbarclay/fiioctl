"""Terminal UI (`fiioctl ui`). Optional: `pip install 'fiioctl[ui]'`.

Four pages down the left: onboard settings, the band editor with a live response
curve, the device's saved EQ slots, and a searchable profile library with curve
previews.

One rule throughout: **arrow to a thing, press + or - to change it.** Nothing is
committed with a button, and the hint line at the bottom always names what is
selected and what the step is, so there is nothing to memorise.

Device I/O runs on worker threads behind a lock, because the HID calls block and
the firmware wants ~100 ms between commands. The UI keeps a local model and
repaints from that, so editing stays responsive instead of waiting for a
read-back after every keystroke.
"""

import threading

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.css.query import NoMatches
from textual.widgets import (
    ContentSwitcher, DataTable, Footer, Header, Input, Label, ListItem, ListView, Static,
)

from . import curve, eqdb, proto, registry
from .device import FiioError

try:
    from . import cloud
except ImportError:  # pragma: no cover
    cloud = None

#: nudge per setting. "eq-gain" is special-cased to the device's own resolution
#: (see Device.gain_step): a 0.5 dB step on hardware that stores whole decibels
#: would make every other keypress look like it did nothing.
STEPS = {
    "volume": 1, "volume-max": 1, "balance": 1, "filter": 1,
    "led": 1, "screen": 1, "mic-volume": 1, "mic-monitor-volume": 1, "mic-nr": 1,
}
ORDER = ["eq-preset", "eq-gain", "volume", "volume-max", "volume-switch", "balance", "filter"]
LABELS = {
    "eq-preset": "preset", "eq-gain": "global gain", "volume": "volume",
    "volume-max": "volume limit", "volume-switch": "line-out fixed", "balance": "balance",
    "filter": "DAC filter", "led": "indicator LED", "screen": "screen orientation",
    "language": "menu language", "brightness": "screen brightness",
    "mic-switch": "microphone", "mic-volume": "mic gain", "mic-monitor": "mic sidetone",
    "mic-monitor-volume": "sidetone volume", "mic-nr": "mic noise reduction",
}
BAND_COLUMNS = ["frequency", "gain", "Q", "filter"]
FILTER_CYCLE = list(proto.FILTERS)
PAGES = [("device", "1  Device"), ("eq", "2  Equaliser"),
         ("slots", "3  EQ slots"), ("lib", "4  Library")]


def freq_step(value):
    """Nudge frequency by something sensible for its magnitude."""
    if value < 100:
        return 1
    if value < 1000:
        return 5
    if value < 10000:
        return 50
    return 250


class FiioApp(App):
    TITLE = "fiioctl"
    CSS = """
    #body { height: 1fr; }
    #nav { width: 18; border-right: solid $panel; }
    #nav > ListItem { padding: 0 1; }
    #pages { width: 1fr; }
    #settings, #slots { height: 1fr; }
    /* tables get the width their columns need; the graph takes the remainder and
       is drawn to fit at paint time (see _plot_width), so it grows with the window */
    #bands { width: 44; }
    #curve { width: 1fr; padding: 0 1; color: $accent; }
    #results { width: 1fr; }
    #preview { width: 50; padding: 0 1; color: $accent; }
    #search { margin: 0 1 1 1; }
    #rename { display: none; margin: 0 1; }
    /* height 3, not 2: border-top eats one row, and the hint is two lines */
    #hint { height: 3; padding: 0 1; border-top: solid $panel; }
    """
    BINDINGS = [
        # plus/minus stay non-priority so they can be typed into text boxes.
        # show=False on anything the sidebar or the hint line already explains,
        # so the footer stays short enough to read.
        Binding("plus,equals_sign", "nudge(1)", "increase", show=False),
        Binding("minus,underscore", "nudge(-1)", "decrease", show=False),
        # DataTable scrolls on PageUp/PageDown, so claim these before it does
        Binding("pageup", "nudge(10)", "+ x10", priority=True, show=False),
        Binding("pagedown", "nudge(-10)", "- x10", priority=True, show=False),
        Binding("1", "page('device')", "device", priority=True, show=False),
        Binding("2", "page('eq')", "equaliser", priority=True, show=False),
        Binding("3", "page('slots')", "slots", priority=True, show=False),
        Binding("4", "page('lib')", "library", priority=True, show=False),
        ("enter", "activate", "load / apply"),
        ("n", "rename", "rename slot"),
        ("s", "save", "save to slot"),
        ("slash", "find", "search"),
        ("u", "undo", "undo"),
        ("r", "reload", "reload"),
        ("escape", "cancel", "cancel"),
        ("question_mark", "help_keys", "keys"),
        ("q", "quit", "quit"),
    ]

    def __init__(self, dac):
        super().__init__()
        self.dac = dac
        self.spec = dac.spec
        self._lock = threading.Lock()
        self.settings = {}
        self.bands = []
        self.slot_names = {}
        self.results = []
        self.previews = {}
        self.undo_stack = []
        self.renaming = False
        #: compose() has finished and the widgets can be queried. on_resize fires
        #: during mount, before that is true.
        self.laid_out = False
        self.sub_title = dac.info.get("product_string") or self.spec.name

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield ListView(*[ListItem(Label(title), id=f"nav-{key}") for key, title in PAGES],
                           id="nav")
            with ContentSwitcher(initial="page-eq", id="pages"):
                with Vertical(id="page-device"):
                    yield DataTable(id="settings", cursor_type="row")
                with Horizontal(id="page-eq"):
                    yield DataTable(id="bands", cursor_type="cell")
                    yield Static(id="curve")
                with Vertical(id="page-slots"):
                    yield DataTable(id="slots", cursor_type="row")
                    yield Input(placeholder="new name, Enter to apply (Esc cancels)",
                                id="rename")
                with Vertical(id="page-lib"):
                    yield Input(placeholder="headphone or preset name, then Enter",
                                id="search")
                    with Horizontal():
                        yield DataTable(id="results", cursor_type="row")
                        yield Static(id="preview")
        yield Static(id="hint")
        yield Footer()

    def on_mount(self):
        self.query_one("#settings", DataTable).add_columns("setting", "value")
        self.query_one("#bands", DataTable).add_columns("#", *BAND_COLUMNS)
        self.query_one("#slots", DataTable).add_columns("slot", "name", "")
        self.query_one("#results", DataTable).add_columns("profile", "source", "by", "bands")
        self.query_one("#nav", ListView).index = 1
        self.query_one("#bands", DataTable).focus()
        self.laid_out = True
        self.reload()

    # -- plumbing -----------------------------------------------------------

    def _io(self, fn, *args, **kwargs):
        with self._lock:
            return fn(*args, **kwargs)

    def _status(self, text, error=False):
        fw = self.settings.get("firmware", "?")
        name = self.dac.info.get("product_string") or self.spec.name
        self.sub_title = f"{name} · fw {fw}  —  {text}"
        if error:
            self.notify(text, severity="error", timeout=6)

    def _page(self):
        try:
            return self.query_one("#pages", ContentSwitcher).current or "page-eq"
        except NoMatches:
            return "page-eq"

    def action_page(self, key):
        try:
            self.query_one("#pages", ContentSwitcher).current = f"page-{key}"
            nav = self.query_one("#nav", ListView)
        except NoMatches:
            return
        nav.index = [k for k, _ in PAGES].index(key)
        focus = {"device": "#settings", "eq": "#bands",
                 "slots": "#slots", "lib": "#results"}[key]
        widget = self.query_one(focus)
        if getattr(widget, "row_count", 1):
            widget.focus()
        self._refresh_hint()

    def on_list_view_highlighted(self, event: ListView.Highlighted):
        # fires when on_mount seeds nav.index, before the pages exist
        if not self.laid_out or event.list_view.id != "nav" or event.item is None:
            return
        key = (event.item.id or "nav-eq").removeprefix("nav-")
        try:
            switcher = self.query_one("#pages", ContentSwitcher)
        except NoMatches:
            return
        if switcher.current != f"page-{key}":
            switcher.current = f"page-{key}"
            self._refresh_hint()

    # -- reading ------------------------------------------------------------

    def action_reload(self):
        self.reload()

    @work(thread=True, group="io")
    def reload(self):
        values, caps = {}, set(self.spec.caps)
        names = ["firmware"] + ORDER + [
            n for n in registry.COMMANDS if n not in ORDER and n != "firmware"
        ]
        for name in names:
            cmd = registry.COMMANDS.get(name)
            if not cmd or not cmd.readable or name in self.spec.unsupported:
                continue
            need = registry.CAPS.get(name)
            if need and need not in caps:
                continue
            try:
                values[name] = self._io(self.dac.get, name, timeout=0.4)
            except FiioError:
                continue
        bands, names_by_slot = [], {}
        try:
            count = self._io(self.dac.band_count)
            bands = [b for b in (self._io(self.dac.get_band, i) for i in range(count)) if b]
        except FiioError as exc:
            self.call_from_thread(self._status, str(exc), True)
        for slot in self.spec.name_slots:
            try:
                names_by_slot[slot] = self._io(self.dac.get_preset_name, slot)
            except FiioError:
                continue
        self.call_from_thread(self._adopt, values, bands, names_by_slot)

    def _adopt(self, values, bands, slot_names):
        self.settings = values
        self.bands = bands
        self.slot_names = slot_names
        self._status(f"{len(bands)} bands")
        self.repaint()

    # -- painting -----------------------------------------------------------

    def _setting_rows(self):
        """Editable settings only: a read-only row would just error on +/-."""
        def editable(name):
            cmd = registry.COMMANDS.get(name)
            return bool(cmd and cmd.writable and cmd.readable)

        ordered = [n for n in ORDER if n in self.settings and editable(n)]
        extra = [n for n in self.settings if n not in ORDER and editable(n)]
        return ordered + sorted(extra)

    def _plot_width(self, selector):
        """Fit the curve to its panel: 6 columns go to the axis labels and edge."""
        try:
            width = self.query_one(selector).size.width
        except NoMatches:
            width = 0
        return max(24, (width or 60) - 8)

    def _format(self, name, value):
        if name == "eq-preset":
            return f"{self._preset_label(value)} ({value})"
        if name == "eq-gain":
            return f"{value:+.1f} dB"
        if name == "volume-switch":
            return "yes" if value else "no"
        if name == "volume" and "volume-max" in self.settings:
            return f"{value} / {self.settings['volume-max']}"
        text = registry.label(name, value)
        return text if text is not None else str(value)

    def _preset_label(self, value):
        if value in self.slot_names and self.slot_names[value]:
            return self.slot_names[value]
        return next((k for k, v in self.spec.presets.items() if v == value), "?")

    def repaint(self):
        if not self.laid_out:
            return
        self._paint_settings()
        self._paint_bands()
        self._paint_slots()
        self._refresh_hint()

    @staticmethod
    def _keep_cursor(table, rows, column=0):
        before = table.cursor_coordinate
        if rows:
            table.cursor_coordinate = Coordinate(
                min(before.row, rows - 1), max(column, min(before.column, 4))
            )

    def _paint_settings(self):
        table = self.query_one("#settings", DataTable)
        before = table.cursor_coordinate
        table.clear()
        for name in self._setting_rows():
            table.add_row(LABELS.get(name, name), self._format(name, self.settings[name]))
        if table.row_count:
            table.cursor_coordinate = Coordinate(min(before.row, table.row_count - 1), 0)

    def _paint_bands(self):
        table = self.query_one("#bands", DataTable)
        before = table.cursor_coordinate
        table.clear()
        for b in self.bands:
            table.add_row(str(b["index"]), f"{b['freq']} Hz", f"{b['gain']:+.1f} dB",
                          f"{b['q']:.2f}", b["filter"])
        if table.row_count:
            table.cursor_coordinate = Coordinate(
                min(before.row, table.row_count - 1), max(1, min(before.column, 4))
            )
        gain = self.settings.get("eq-gain")
        head = f"global gain {gain:+.1f} dB" if isinstance(gain, (int, float)) else ""
        body = "\n".join(
            curve.plot(self.bands, height=10, width=self._plot_width("#curve"))
        ) if self.bands else "no bands read"
        self.query_one("#curve", Static).update(f"{head}\n{body}")

    def on_resize(self, _event):
        # fires during mount too, when the widgets do not exist yet
        if not self.laid_out:
            return
        self._paint_bands()
        self._show_preview()

    def _paint_slots(self):
        table = self.query_one("#slots", DataTable)
        before = table.cursor_coordinate
        table.clear()
        active = self.settings.get("eq-preset")
        for label, value in sorted(self.spec.presets.items(), key=lambda kv: kv[1]):
            name = self.slot_names.get(value) or label
            kind = "user" if value in self.spec.name_slots else "factory"
            table.add_row(f"{value}", name, ("active  " if value == active else "") + kind)
        if table.row_count:
            table.cursor_coordinate = Coordinate(min(before.row, table.row_count - 1), 0)

    def _refresh_hint(self):
        # ListView.Highlighted and Resize both fire while the screen is still
        # being composed, when none of these widgets exist yet.
        if not self.laid_out:
            return
        try:
            panel = self.query_one("#hint", Static)
        except NoMatches:
            return
        panel.update(self._hint())

    def on_data_table_cell_highlighted(self, _event):
        self._refresh_hint()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        if event.data_table.id == "results":
            self._show_preview()
        self._refresh_hint()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        """Enter on a row. DataTable consumes the key, so dispatch from its event."""
        if event.data_table.id in ("slots", "results"):
            self.action_activate()

    def on_descendant_focus(self, _event):
        self._refresh_hint()

    # -- hint ---------------------------------------------------------------

    def _hint(self):
        if self.renaming:
            return "[b]rename[/b]  type a name and press Enter   Esc cancels"
        page = self._page()
        if page == "page-slots":
            slot = self._selected_slot()
            if slot is None:
                return "no slots   [b]1-4[/b] pages"
            return (f"selected  [b]slot {slot[0]}[/b] = {slot[1]}\n"
                    f"[b]Enter[/b] load it   [b]n[/b] rename   [b]s[/b] save current EQ here"
                    f"   [b]1-4[/b] pages")
        if page == "page-lib":
            entry = self._selected_result()
            if not entry:
                return ("[b]/[/b] or Tab to the box, type a name, press Enter\n"
                        "searches AutoEq and FiiO's catalogue   [b]1-4[/b] pages")
            return (f"selected  [b]{entry['name']}[/b] ({entry['source']})\n"
                    f"[b]Enter[/b] apply to the device   [b]/[/b] new search   [b]1-4[/b] pages")
        target = self._target()
        if not target:
            return "nothing selected   [b]Tab[/b] switch   [b]1-4[/b] pages"
        line1 = f"selected  [b]{target['what']}[/b] = {target['shown']}"
        if target["step"]:
            line2 = (f"[b]+[/b] / [b]-[/b] change by {target['step']}"
                     f"   [b]PgUp[/b] / [b]PgDn[/b] by {target['big']}")
        else:
            line2 = "[b]+[/b] / [b]-[/b] cycle through the options"
        return f"{line1}\n{line2}   [b]Tab[/b] switch   [b]1-4[/b] pages   [b]?[/b] keys"

    # -- selection ----------------------------------------------------------

    def _target(self):
        focused = self.focused
        if focused is None:
            return None
        if focused.id == "settings":
            rows = self._setting_rows()
            row = focused.cursor_coordinate.row
            if not rows or row >= len(rows):
                return None
            name = rows[row]
            cmd = registry.COMMANDS[name]
            if name == "eq-preset" or cmd.codec == "bool" or cmd.enum:
                step = big = None
            else:
                unit = " dB" if name == "eq-gain" else ""
                raw = self._step_for(name)
                step, big = f"{raw:g}{unit}", f"{raw * 10:g}{unit}"
            return {"kind": "setting", "name": name, "what": LABELS.get(name, name),
                    "shown": self._format(name, self.settings[name]),
                    "step": step, "big": big}
        if focused.id == "bands" and self.bands:
            coord = focused.cursor_coordinate
            if coord.row >= len(self.bands):
                return None
            band = self.bands[coord.row]
            field = BAND_COLUMNS[max(1, coord.column) - 1]
            shown = {"frequency": f"{band['freq']} Hz", "gain": f"{band['gain']:+.1f} dB",
                     "Q": f"{band['q']:.2f}", "filter": band["filter"]}[field]
            step = {"frequency": f"{freq_step(band['freq'])} Hz", "gain": "0.5 dB",
                    "Q": "0.05", "filter": None}[field]
            big = {"frequency": f"{freq_step(band['freq']) * 10} Hz", "gain": "5 dB",
                   "Q": "0.5", "filter": None}[field]
            return {"kind": "band", "index": band["index"], "field": field,
                    "what": f"band {band['index']} · {field}", "shown": shown,
                    "step": step, "big": big}
        return None

    def _selected_slot(self):
        try:
            table = self.query_one("#slots", DataTable)
        except NoMatches:
            return None
        if table.row_count == 0:
            return None
        row = table.get_row_at(table.cursor_row)
        return int(row[0]), row[1]

    def _selected_result(self):
        try:
            table = self.query_one("#results", DataTable)
        except NoMatches:
            return None
        if not self.results or table.row_count == 0:
            return None
        index = table.cursor_row
        return self.results[index] if 0 <= index < len(self.results) else None

    # -- editing ------------------------------------------------------------

    def action_nudge(self, direction: int):
        if self.renaming:
            return
        target = self._target()
        if not target:
            return
        if target["kind"] == "setting":
            self._nudge_setting(target["name"], direction)
        else:
            self._nudge_band(target["index"], target["field"], direction)

    def _step_for(self, name):
        if name == "eq-gain":
            return self.spec.gain_step or 0.1
        return STEPS.get(name, 1)

    def _nudge_setting(self, name, direction):
        value = self.settings[name]
        cmd = registry.COMMANDS[name]
        if name == "eq-preset":
            options = sorted(set(self.spec.presets.values()))
            idx = options.index(value) if value in options else 0
            new = options[(idx + (1 if direction > 0 else -1)) % len(options)]
        elif cmd.enum:
            # cycle the declared options; the firmware rejects anything else
            options = sorted(set(cmd.enum.values()))
            idx = options.index(value) if value in options else 0
            new = options[(idx + (1 if direction > 0 else -1)) % len(options)]
        elif cmd.codec == "bool":
            new = not value
        else:
            step = self._step_for(name) * direction
            new = value + step
            if cmd.rng:
                lo, hi = cmd.rng
                new = max(lo, min(hi, new))
            elif name == "volume":
                new = max(0, min(self.settings.get("volume-max", 255), new))
            else:
                new = max(0, new)
            new = round(new, 1) if isinstance(step, float) else int(new)
        if new == value:
            return
        self.undo_stack.append((LABELS.get(name, name), ("setting", name, value)))
        self.settings[name] = new
        self.repaint()
        self.push_setting(name, new)

    def _nudge_band(self, index, field, direction):
        band = dict(self.bands[index])
        before = dict(band)
        if field == "frequency":
            band["freq"] = max(20, min(20000, band["freq"] + freq_step(band["freq"]) * direction))
        elif field == "gain":
            lo, hi = self.spec.gain_range
            band["gain"] = round(max(lo, min(hi, band["gain"] + 0.5 * direction)), 1)
        elif field == "Q":
            lo, hi = self.spec.q_range
            band["q"] = round(max(lo, min(hi, band["q"] + 0.05 * direction)), 2)
        else:
            current = band["filter"] if band["filter"] in FILTER_CYCLE else "peak"
            idx = FILTER_CYCLE.index(current)
            band["filter"] = FILTER_CYCLE[(idx + (1 if direction > 0 else -1)) % len(FILTER_CYCLE)]
        if band == before:
            return
        self.undo_stack.append((f"band {index} {field}", ("band", index, before)))
        self.bands[index] = band
        self.repaint()
        self.push_band(band)

    @work(thread=True, group="io")
    def push_setting(self, name, value):
        try:
            self._io(self.dac.set, name, value)
        except (FiioError, ValueError) as exc:
            self.call_from_thread(self._status, str(exc), True)
            return
        self.call_from_thread(self._status, f"{LABELS.get(name, name)} = {value}")

    @work(thread=True, group="io")
    def push_band(self, band):
        try:
            self._io(self.dac.set_band, band["index"], band["freq"], band["gain"],
                     band["q"], band["filter"])
        except (FiioError, ValueError, KeyError) as exc:
            self.call_from_thread(self._status, str(exc), True)
            return
        self.call_from_thread(
            self._status,
            f"band {band['index']}: {band['freq']} Hz {band['gain']:+.1f} dB "
            f"Q {band['q']:.2f} {band['filter']}")

    def action_undo(self):
        if not self.undo_stack:
            self.notify("nothing to undo")
            return
        label, entry = self.undo_stack.pop()
        if entry[0] == "setting":
            _, name, value = entry
            self.settings[name] = value
            self.repaint()
            self.push_setting(name, value)
        else:
            _, index, band = entry
            self.bands[index] = band
            self.repaint()
            self.push_band(band)
        self._status(f"undid {label}")

    # -- slots --------------------------------------------------------------

    def action_activate(self):
        page = self._page()
        if page == "page-slots":
            slot = self._selected_slot()
            if slot:
                self._nudge_to_preset(slot[0])
        elif page == "page-lib":
            entry = self._selected_result()
            if entry:
                self.apply_profile(entry)

    def _nudge_to_preset(self, value):
        previous = self.settings.get("eq-preset")
        if previous == value:
            return
        self.undo_stack.append(("preset", ("setting", "eq-preset", previous)))
        self.settings["eq-preset"] = value
        self.repaint()
        self.push_setting("eq-preset", value)
        self.load_bands_after_switch()

    @work(thread=True, group="io")
    def load_bands_after_switch(self):
        try:
            count = self._io(self.dac.band_count)
            bands = [b for b in (self._io(self.dac.get_band, i) for i in range(count)) if b]
            gain = self._io(self.dac.get, "eq-gain")
        except FiioError as exc:
            self.call_from_thread(self._status, str(exc), True)
            return
        self.call_from_thread(self._after_switch, bands, gain)

    def _after_switch(self, bands, gain):
        self.bands = bands
        self.settings["eq-gain"] = gain
        self.repaint()
        self._status(f"loaded preset {self.settings.get('eq-preset')}")

    def action_save(self):
        slot = None
        if self._page() == "page-slots":
            picked = self._selected_slot()
            slot = picked[0] if picked else None
        else:
            slot = self.settings.get("eq-preset")
        if slot is None:
            return
        if self.spec.name_slots and slot not in self.spec.name_slots:
            self.notify(
                f"slot {slot} is a factory preset; pick one of "
                f"{', '.join(str(s) for s in self.spec.name_slots)} on the EQ slots page",
                severity="warning", timeout=8)
            return
        self.save_to(slot)

    @work(thread=True, group="io")
    def save_to(self, slot):
        try:
            self._io(self.dac.set, "eq-save", slot)
        except FiioError as exc:
            self.call_from_thread(self._status, str(exc), True)
            return
        self.call_from_thread(self._status, f"saved current EQ to slot {slot}")

    def action_rename(self):
        if self._page() != "page-slots":
            return
        picked = self._selected_slot()
        if not picked or picked[0] not in self.spec.name_slots:
            self.notify("only user slots can be renamed", severity="warning")
            return
        self.renaming = True
        box = self.query_one("#rename", Input)
        box.styles.display = "block"
        box.value = picked[1]
        box.focus()
        self._refresh_hint()

    @work(thread=True, group="io")
    def push_name(self, slot, text):
        try:
            clean = self._io(self.dac.set_preset_name, slot, text)
        except (FiioError, ValueError) as exc:
            self.call_from_thread(self._status, str(exc), True)
            return
        self.call_from_thread(self._after_rename, slot, clean)

    def _after_rename(self, slot, clean):
        self.slot_names[slot] = clean
        self.repaint()
        self._status(f"slot {slot} renamed to {clean}")

    # -- library ------------------------------------------------------------

    def action_find(self):
        self.action_page("lib")
        self.query_one("#search", Input).focus()

    def action_cancel(self):
        if self.renaming:
            self.renaming = False
            self.query_one("#rename", Input).styles.display = "none"
            self.query_one("#slots", DataTable).focus()
            self._refresh_hint()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "search":
            query = event.value.strip()
            if query:
                self._status(f"searching for {query!r}")
                self.run_search(query)
        elif event.input.id == "rename":
            picked = self._selected_slot()
            self.renaming = False
            self.query_one("#rename", Input).styles.display = "none"
            self.query_one("#slots", DataTable).focus()
            if picked and event.value.strip():
                self.push_name(picked[0], event.value.strip())

    @work(thread=True, group="net")
    def run_search(self, query):
        """Search all three sources: FiiO presets, FiiO measurements, AutoEq."""
        found, problems = [], []
        if cloud is not None and self.spec.cloud_id:
            try:
                found.extend(cloud.presets(self.spec.cloud_id, query))
            except Exception as exc:  # noqa: BLE001 - a catalogue failure is non-fatal
                problems.append(str(exc))
        if cloud is not None:
            try:
                for row in cloud.find_measurements(query, limit=25):
                    variant = f" ({row['config']})" if row.get("config") else ""
                    found.append({"name": f"{row['productName']}{variant}",
                                  "source": "fiio-autoeq",
                                  "rig": row.get("source") or "",
                                  "bands": None, "meas_id": row["id"]})
            except Exception as exc:  # noqa: BLE001
                problems.append(str(exc))
        try:
            for e in eqdb.search(query, limit=25):
                found.append({"name": e["name"], "source": "autoeq",
                              "rig": e.get("source", ""), "bands": None, "entry": e})
        except OSError as exc:
            problems.append(f"AutoEq unreachable: {exc}")
        self.call_from_thread(self._show_results, found, problems)

    def _resolve(self, entry, count):
        """Fill in an entry's bands and preamp. Runs on a worker: may hit the network."""
        if entry.get("bands") is not None:
            return entry
        if entry.get("meas_id") is not None:
            if cloud is None:
                raise FiioError("the FiiO catalogue needs: pip install 'fiioctl[cloud]'")
            prof = cloud.auto_eq(entry["meas_id"], bands=count)
            entry.update(bands=prof["bands"], preamp=prof["preamp"],
                         description=prof["description"])
        else:
            prof = eqdb.profile(entry["entry"])
            entry.update(bands=prof["bands"], preamp=prof["preamp"])
        return entry

    @staticmethod
    def _key(entry):
        if entry.get("meas_id") is not None:
            return ("m", entry["meas_id"])
        return ("a", entry["entry"]["path"])

    def _show_results(self, found, problems):
        self.results = found
        table = self.query_one("#results", DataTable)
        table.clear()
        for p in found:
            table.add_row(p["name"][:32], p["source"], (p.get("rig") or "")[:14],
                          str(len(p["bands"])) if p["bands"] else "-")
        self._status(f"{len(found)} result(s)" + ("; " + "; ".join(problems) if problems else ""))
        if problems and not found:
            self.notify(problems[0], severity="error", timeout=8)
        if found:
            table.focus()
            self._show_preview()
        else:
            self.query_one("#preview", Static).update("no match")

    def _show_preview(self):
        try:
            panel = self.query_one("#preview", Static)
        except NoMatches:
            return
        entry = self._selected_result()
        if not entry:
            panel.update("")
            return
        bands = entry.get("bands")
        if bands is None:
            cached = self.previews.get(self._key(entry))
            if cached:
                bands, entry["bands"] = cached["bands"], cached["bands"]
                entry.setdefault("preamp", cached["preamp"])
            else:
                panel.update(f"{entry['name']}\n\ncomputing profile...")
                self.fetch_preview(entry)
                return
        head = f"{entry['name']}\npreamp {entry.get('preamp', 0.0):+.1f} dB · {len(bands)} bands"
        note = entry.get("description") or ""
        body = "\n".join(curve.plot(bands, height=9, width=self._plot_width("#preview")))
        panel.update(f"{head}\n{body}" + (f"\n{note[:160]}" if note else ""))

    @work(thread=True, group="net")
    def fetch_preview(self, entry):
        count = len(self.bands) or 10
        try:
            self._resolve(entry, count)
        except (OSError, ValueError, FiioError, Exception) as exc:  # noqa: BLE001
            self.call_from_thread(self._status, f"preview failed: {exc}", True)
            return
        self.call_from_thread(self._adopt_preview, entry)

    def _adopt_preview(self, entry):
        self.previews[self._key(entry)] = {"bands": entry["bands"],
                                           "preamp": entry.get("preamp", 0.0)}
        if self._selected_result() is entry:
            self._show_preview()

    @work(thread=True, group="io")
    def apply_profile(self, entry):
        try:
            count = self._io(self.dac.band_count)
            self._resolve(entry, count)
            lo, hi = self.spec.gain_range
            preamp = max(lo, min(hi, float(entry.get("preamp") or 0.0)))
            self._io(self.dac.set, "eq-gain", preamp)
            chosen = entry["bands"][:count]
            for i, b in enumerate(chosen):
                self._io(self.dac.set_band, i, b["freq"], max(lo, min(hi, b["gain"])),
                         b["q"], b["filter"])
            for i in range(len(chosen), count):
                self._io(self.dac.set_band, i, 1000, 0.0, 0.7, "peak")
        except (FiioError, OSError, ValueError) as exc:
            self.call_from_thread(self._status, str(exc), True)
            return
        self.call_from_thread(self._after_apply, entry, chosen, preamp, count)

    def _after_apply(self, entry, chosen, preamp, count):
        self.settings["eq-gain"] = preamp
        self.bands = [{"index": i, "freq": int(b["freq"]), "gain": float(b["gain"]),
                       "q": float(b["q"]), "filter": b["filter"]}
                      for i, b in enumerate(chosen)]
        self.bands += [{"index": i, "freq": 1000, "gain": 0.0, "q": 0.7, "filter": "peak"}
                       for i in range(len(chosen), count)]
        dropped = len(entry["bands"]) - len(chosen)
        self.action_page("eq")
        self._status(f"applied '{entry['name']}'"
                     + (f" ({dropped} band(s) dropped, device has {count})" if dropped else "")
                     + " — press s to save it into a user slot")

    # -- help ---------------------------------------------------------------

    def action_help_keys(self):
        self.notify(
            "1-4 pages · arrows move · Tab switch pane\n"
            "+ / - change the selected value · PgUp / PgDn by ten steps\n"
            "Enter load a slot or apply a profile · n rename slot · s save EQ to slot\n"
            "/ search · u undo · r reload · q quit",
            title="keys", timeout=16)


def run(dac):
    FiioApp(dac).run()
    return 0
