"""Headless TUI tests driven by Textual's pilot. No hardware, no terminal, no network.

The interaction contract under test: arrow to a parameter, press + or - to change
it, and the hint line always says what is selected and by how much it will move.
"""

import time

import pytest

pytest.importorskip("textual")

from textual.coordinate import Coordinate  # noqa: E402
from textual.widgets import DataTable, Input, Static  # noqa: E402

from fiioctl import registry  # noqa: E402
from fiioctl import ui as ui_mod  # noqa: E402
from fiioctl.device import Dac, FiioError  # noqa: E402
from fiioctl.ui import FiioApp  # noqa: E402

FACTORY = [(32, 3.0), (64, 3.0), (125, 2.5), (250, -0.5), (500, -2.5),
           (1000, 2.0), (2000, 2.5), (4000, -2.0), (8000, 2.0), (16000, 3.0)]


class FakeDac:
    """Stands in for a KA15, recording every write."""

    # the real thing, so these tests cover what the TUI actually sends
    snap_gain = Dac.snap_gain
    write_profile = Dac.write_profile

    def __init__(self):
        self.spec = registry.KA15
        self.info = {"product_string": "FIIO KA15"}
        self.writes = []
        self.bands = [
            {"index": i, "freq": f, "gain": g, "q": 0.71, "filter": "peak"}
            for i, (f, g) in enumerate(FACTORY)
        ]
        self.names = {7: "FD15", 8: "FF5", 9: "FH3"}
        self.values = {
            "firmware": "1.1.6", "eq-preset": 6, "eq-gain": -7.0, "volume": 60,
            "volume-max": 120, "volume-switch": False, "balance": 0, "filter": 0,
            "eq-count": 10,
        }

    def get(self, name, timeout=None):
        if name not in self.values or name in self.spec.unsupported:
            raise FiioError(f"{name} unsupported")
        return self.values[name]

    def set(self, name, value):
        if name in self.spec.unsupported:
            raise FiioError(f"{name} unsupported")
        self.values[name] = value
        self.writes.append((name, value))
        return value

    def band_count(self):
        return len(self.bands)

    def get_band(self, index):
        return dict(self.bands[index])

    def set_band(self, index, freq, gain, q, filter_type=0):
        self.bands[index] = {"index": index, "freq": int(freq), "gain": float(gain),
                             "q": float(q), "filter": filter_type}
        self.writes.append(("band", index, int(freq), float(gain), float(q), filter_type))

    def get_preset_name(self, slot):
        return self.names.get(slot, "")

    def set_preset_name(self, slot, text):
        clean = "".join(c for c in text if c.isalnum())[: self.spec.name_max]
        if not clean:
            raise FiioError("empty name")
        self.names[slot] = clean
        self.writes.append(("name", slot, clean))
        return clean


@pytest.fixture
def dac():
    return FakeDac()


class StubEqdb:
    """Stands in for the eqdb module inside the ui namespace.

    Replacing `ui.eqdb` wholesale rather than patching attributes on the real
    module keeps two hazards away: other test files that call `eqdb.search`
    directly are unaffected, and a worker thread that outlives its test cannot
    reach the network once the patch is reverted.
    """

    @staticmethod
    def search(query, limit=40):
        return [{"name": "Sennheiser HD 650", "path": "oratory1990/over-ear/HD 650",
                 "source": "oratory1990", "rig": "over-ear"}]

    @staticmethod
    def profile(entry):
        return {"preamp": -6.1,
                "bands": [{"freq": 105, "gain": 6.4, "q": 0.7, "filter": "low-shelf"},
                          {"freq": 3300, "gain": -3.1, "q": 3.0, "filter": "peak"}]}

    @staticmethod
    def squash(text):
        from fiioctl.eqdb import squash
        return squash(text)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Keep the library page from touching the network."""
    monkeypatch.setattr(ui_mod, "cloud", None)
    monkeypatch.setattr(ui_mod, "eqdb", StubEqdb)


async def ready(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


async def until(app, pilot, predicate, tries=80):
    """Wait for a condition rather than for 'the workers'.

    Several flows chain workers (search -> preview -> apply), and
    wait_for_complete() only waits for the ones already scheduled, so it can
    return before the next link has even started.
    """
    for _ in range(tries):
        if predicate():
            return True
        await ready(app, pilot)
    return predicate()


def hint(app):
    return str(app.query_one("#hint", Static).render())


async def goto(app, pilot, key):
    app.action_page(key)
    await ready(app, pilot)


async def select_band(app, pilot, row, column):
    await goto(app, pilot, "eq")
    table = app.query_one("#bands", DataTable)
    table.focus()
    table.cursor_coordinate = Coordinate(row, column)
    await pilot.pause()


async def select_setting(app, pilot, name):
    await goto(app, pilot, "device")
    table = app.query_one("#settings", DataTable)
    table.focus()
    table.cursor_coordinate = Coordinate(app._setting_rows().index(name), 0)
    await pilot.pause()


async def select_slot(app, pilot, slot):
    await goto(app, pilot, "slots")
    table = app.query_one("#slots", DataTable)
    table.focus()
    order = sorted(app.spec.presets.values())
    table.cursor_coordinate = Coordinate(order.index(slot), 0)
    await pilot.pause()


# -- structure ----------------------------------------------------------------


async def test_four_pages_populate(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        assert app.query_one("#bands", DataTable).row_count == 10
        await goto(app, pilot, "device")
        assert app.query_one("#settings", DataTable).row_count >= 6
        await goto(app, pilot, "slots")
        assert app.query_one("#slots", DataTable).row_count == len(app.spec.presets)
        drawn = str(app.query_one("#curve", Static).render())
        assert "┼" in drawn and "━" in drawn


async def test_number_keys_switch_pages(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await pilot.press("3")
        await ready(app, pilot)
        assert app._page() == "page-slots"
        await pilot.press("1")
        await ready(app, pilot)
        assert app._page() == "page-device"


async def test_slot_names_are_read_from_the_device(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        assert app.slot_names == {7: "FD15", 8: "FF5", 9: "FH3"}
        await goto(app, pilot, "slots")
        table = app.query_one("#slots", DataTable)
        rendered = [table.get_row_at(i)[1] for i in range(table.row_count)]
        assert "FD15" in rendered and "FH3" in rendered


async def test_unsupported_command_is_not_listed(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        assert "eq-switch" not in app.settings


# -- hints --------------------------------------------------------------------


async def test_hint_names_selected_band_field_and_step(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 0, 2)
        text = hint(app)
        assert "band 0" in text and "gain" in text
        assert "+3.0 dB" in text and "0.5 dB" in text


async def test_hint_names_selected_setting(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_setting(app, pilot, "eq-gain")
        text = hint(app)
        assert "global gain" in text and "-7.0 dB" in text


async def test_filter_column_hint_says_cycle(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 0, 4)
        assert "cycle" in hint(app)


async def test_slots_page_hint_explains_its_keys(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await goto(app, pilot, "slots")
        text = hint(app)
        assert "load" in text and "rename" in text and "save" in text


# -- editing ------------------------------------------------------------------


async def test_plus_raises_band_gain_and_writes_once(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 0, 2)
        await pilot.press("plus")
        await ready(app, pilot)
        assert ("band", 0, 32, 3.5, 0.71, "peak") in dac.writes
        assert len([w for w in dac.writes if w[0] == "band"]) == 1


async def test_band_gain_clamps_to_device_range(dac):
    dac.bands[0]["gain"] = 12.0
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 0, 2)
        await pilot.press("plus")
        await ready(app, pilot)
        assert not [w for w in dac.writes if w[0] == "band"]


async def test_frequency_step_scales_with_magnitude(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 0, 1)
        await pilot.press("plus")
        await ready(app, pilot)
        assert dac.bands[0]["freq"] == 33
        await select_band(app, pilot, 9, 1)
        await pilot.press("plus")
        await ready(app, pilot)
        assert dac.bands[9]["freq"] == 16250


async def test_pageup_moves_ten_steps(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 3, 2)
        await pilot.press("pageup")
        await ready(app, pilot)
        assert dac.bands[3]["gain"] == 4.5


async def test_filter_column_cycles(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 0, 4)
        await pilot.press("plus")
        await ready(app, pilot)
        assert dac.bands[0]["filter"] == "low-shelf"


async def test_q_column_steps_by_hundredths(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 0, 3)
        await pilot.press("plus")
        await ready(app, pilot)
        assert dac.bands[0]["q"] == 0.76


async def test_bool_setting_toggles(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_setting(app, pilot, "volume-switch")
        await pilot.press("plus")
        await ready(app, pilot)
        assert ("volume-switch", True) in dac.writes


async def test_volume_clamps_to_reported_max(dac):
    dac.values["volume"] = 120
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_setting(app, pilot, "volume")
        await pilot.press("plus")
        await ready(app, pilot)
        assert not [w for w in dac.writes if w[0] == "volume"]


async def test_undo_restores_previous_band_value(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 0, 2)
        await pilot.press("plus")
        await ready(app, pilot)
        assert dac.bands[0]["gain"] == 3.5
        await pilot.press("u")
        await ready(app, pilot)
        assert dac.bands[0]["gain"] == 3.0


async def test_device_writes_land_in_the_order_they_were_made(dac, monkeypatch):
    """The earliest write is the slowest, so a race would leave it last."""
    original = dac.set_band
    delays = iter([0.15, 0.05])

    def slow(index, freq, gain, q, filter_type=0):
        time.sleep(next(delays, 0))
        original(index, freq, gain, q, filter_type)

    monkeypatch.setattr(dac, "set_band", slow)
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_band(app, pilot, 0, 2)
        for _ in range(4):
            await pilot.press("plus")
        assert await until(app, pilot, lambda: len(dac.writes) == 4)
        assert [w[3] for w in dac.writes] == [3.5, 4.0, 4.5, 5.0]


# -- slots page ---------------------------------------------------------------


async def test_enter_on_a_slot_loads_it(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_slot(app, pilot, 8)
        await pilot.press("enter")
        assert await until(app, pilot, lambda: ("eq-preset", 8) in dac.writes)


async def test_rename_writes_the_new_name(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_slot(app, pilot, 9)
        await pilot.press("n")
        await ready(app, pilot)
        assert app.renaming is True
        box = app.query_one("#rename", Input)
        box.value = "FT1"
        await box.action_submit()
        # wait on the UI state, not on the write record: the worker appends to
        # `writes` before the callback that updates slot_names has run
        assert await until(app, pilot, lambda: app.slot_names.get(9) == "FT1")
        assert ("name", 9, "FT1") in dac.writes


async def test_factory_slots_cannot_be_renamed(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_slot(app, pilot, 2)  # rock, a factory preset
        await pilot.press("n")
        await ready(app, pilot)
        assert app.renaming is False
        assert not [w for w in dac.writes if w[0] == "name"]


async def test_save_refuses_a_factory_slot(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_slot(app, pilot, 2)
        await pilot.press("s")
        await ready(app, pilot)
        assert not [w for w in dac.writes if w[0] == "eq-save"]


async def test_save_writes_to_a_user_slot(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await select_slot(app, pilot, 8)
        await pilot.press("s")
        await ready(app, pilot)
        assert ("eq-save", 8) in dac.writes


# -- library page -------------------------------------------------------------


async def test_search_lists_results_and_previews_the_curve(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await goto(app, pilot, "lib")
        box = app.query_one("#search", Input)
        box.value = "hd 650"
        await box.action_submit()
        assert await until(app, pilot,
                           lambda: app.query_one("#results", DataTable).row_count == 1)
        # the profile is fetched by a second worker; the panel shows a
        # placeholder until it lands
        assert await until(app, pilot,
                           lambda: "┼" in str(app.query_one("#preview", Static).render()))
        preview = str(app.query_one("#preview", Static).render())
        assert "HD 650" in preview
        assert "-6.1" in preview       # preamp came from the fetched profile


async def test_enter_applies_a_profile_to_the_device(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await goto(app, pilot, "lib")
        box = app.query_one("#search", Input)
        box.value = "hd 650"
        await box.action_submit()
        assert await until(app, pilot,
                           lambda: app.query_one("#results", DataTable).row_count == 1)
        await pilot.press("enter")
        # the KA15 stores whole decibels, and -6.1 must round away from clipping
        assert await until(app, pilot, lambda: ("eq-gain", -7.0) in dac.writes)
        assert await until(app, pilot, lambda: app._page() == "page-eq")
        assert dac.bands[0]["freq"] == 105 and dac.bands[0]["filter"] == "low-shelf"
        # the profile had 2 bands; the remaining 8 must be flattened
        assert all(b["gain"] == 0.0 for b in dac.bands[2:])
        # what is on screen has to be what is now on the device
        assert app.settings["eq-gain"] == -7.0
        table = app.query_one("#bands", DataTable)
        assert table.get_row_at(0)[1] == "105 Hz" and table.get_row_at(0)[4] == "low-shelf"
        assert table.get_row_at(2)[2] == "+0.0 dB"


async def test_reload_never_writes(dac):
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await pilot.press("r")
        await ready(app, pilot)
        assert dac.writes == []


# -- worker failures ----------------------------------------------------------


async def test_unanticipated_worker_failure_is_reported_not_fatal(dac, monkeypatch):
    """What @reporting exists for.

    `save_to` previously caught only FiioError, so anything else - here a
    RuntimeError, which no worker's old except tuple listed - took the whole app
    down instead of reaching the status line.
    """
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)

        original = dac.set

        def refuse(name, value):
            if name == "eq-save":
                raise RuntimeError("firmware said no")
            return original(name, value)

        monkeypatch.setattr(dac, "set", refuse)

        await select_slot(app, pilot, 8)
        await pilot.press("s")
        assert await until(app, pilot, lambda: "firmware said no" in app.sub_title)
        assert app.is_running


async def test_the_app_still_works_after_a_worker_fails(dac, monkeypatch):
    """Reporting a failure must not leave the UI wedged."""
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)

        original = dac.set

        def refuse(name, value):
            if name == "eq-save":
                raise RuntimeError("nope")
            return original(name, value)

        monkeypatch.setattr(dac, "set", refuse)
        await select_slot(app, pilot, 8)
        await pilot.press("s")
        assert await until(app, pilot, lambda: "nope" in app.sub_title)

        # a normal edit still goes through afterwards
        await select_band(app, pilot, 0, 2)
        await pilot.press("plus")
        assert await until(app, pilot, lambda: any(w[0] == "band" for w in dac.writes))


async def test_a_failing_read_does_not_stop_the_reload(dac, monkeypatch):
    """reload() gained the decorator too; a broken read must not kill startup."""
    def refuse(_name, timeout=None):
        raise RuntimeError("read exploded")

    monkeypatch.setattr(dac, "get", refuse)
    app = FiioApp(dac)
    async with app.run_test() as pilot:
        await ready(app, pilot)
        assert app.is_running
